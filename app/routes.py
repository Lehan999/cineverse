from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import db
from app.forms import LoginForm, RegistrationForm, ReviewForm
from app.models import Cast, Crew, Favorite, Movie, Review, TMDBCacheEntry, User
from app.tmdb import TMDBError, movie_details, movie_genres, movies_by_genre, popular_movies, search_movies

main = Blueprint("main", __name__)


@main.route("/")
def home():
    try:
        movies = popular_movies()
        movies = _cache_movie_posters(movies)
        _cache_tmdb_movies("popular", movies)
        tmdb_error = None
    except TMDBError as error:
        movies = _read_tmdb_cache("popular", []) or _cached_tmdb_movie_pool() or _local_movies()
        tmdb_error = str(error)

    featured_movies = []
    notices = []
    avengers_loaded = False
    for query, title, preferred_year in (
        ("Ramayana", "Ramayana", "2026"),
        ("Avengers: Doomsday", "Avengers: Doomsday", None),
    ):
        try:
            results = search_movies(query)
            results = _cache_movie_posters(results)
            _cache_tmdb_movies(f"search:{query.casefold()}", results)
        except TMDBError:
            results = _read_tmdb_cache(f"search:{query.casefold()}", [])
            if not results:
                results = [movie for movie in _cached_tmdb_movie_pool() if title.casefold() in movie["title"].casefold()]
            if not results:
                notices.append(f"{title} could not be loaded from TMDB or saved movie data.")
                continue
            notices.append(f"Showing saved TMDB information for {title} while offline.")

        match = _select_featured_result(results, title, preferred_year)
        if match:
            featured_movies.append(match)
            if title == "Avengers: Doomsday":
                avengers_loaded = True
        else:
            notices.append(f"{title} was not found in TMDB search.")

    # Keep the hero useful when either requested movie is unavailable from TMDB.
    if not avengers_loaded and movies:
        featured_movies.append(movies[0])

    return render_template(
        "home.html",
        movies=movies,
        featured_movies=featured_movies,
        featured_notice=" ".join(notices) if notices else None,
        tmdb_error=tmdb_error,
    )


def _select_featured_result(results, expected_title, preferred_year=None):
    def normalize(title):
        return "".join(character for character in title.casefold() if character.isalnum())

    expected = normalize(expected_title)
    matching_titles = [movie for movie in results if normalize(movie.get("title", "")).startswith(expected)]
    if preferred_year:
        year_match = next(
            (movie for movie in matching_titles if (movie.get("release_date") or "").startswith(preferred_year)),
            None,
        )
        if year_match:
            return year_match
    return next((movie for movie in matching_titles if normalize(movie["title"]) == expected), None) or next(
        iter(matching_titles), None
    )


@main.route("/search")
def search():
    query = request.args.get("q", "").strip()[:100]
    if not query:
        return render_template("home.html", movies=[], featured_movies=[], query=query, tmdb_error="Enter a movie title to search.")
    try:
        movies = search_movies(query)
        movies = _cache_movie_posters(movies)
        _cache_tmdb_movies(f"search:{query.casefold()}", movies)
        tmdb_error = None
    except TMDBError as error:
        movies = _read_tmdb_cache(f"search:{query.casefold()}", [])
        if not movies:
            movies = [movie for movie in _cached_tmdb_movie_pool() if query.casefold() in movie["title"].casefold()]
        if not movies:
            movies = [movie for movie in _local_movies() if query.casefold() in movie["title"].casefold()]
        tmdb_error = str(error)
    return render_template(
        "home.html",
        movies=movies,
        featured_movies=[movies[0]] if movies else [],
        query=query,
        tmdb_error=tmdb_error,
    )


@main.get("/genres")
def genres():
    selected_id = request.args.get("genre", "").strip()
    selected_genre = None
    movies = []
    genre_rows = []
    tmdb_error = None
    genre_list = []

    try:
        genre_list = movie_genres()
        genres_by_name = {genre["name"].casefold(): genre for genre in genre_list}
        if selected_id:
            selected_genre = next(
                (genre for genre in genre_list if str(genre["id"]) == selected_id),
                None,
            )
            if selected_genre is None:
                tmdb_error = "Choose a genre from the list."
            else:
                movies = movies_by_genre(selected_genre["id"])
                genre_rows = [{"name": selected_genre["name"], "movies": movies}]
        else:
            shelf_names = ["Action", "Comedy", "Drama", "Adventure"]
            shelf_genres = [
                genres_by_name[name.casefold()]
                for name in shelf_names
                if name.casefold() in genres_by_name
            ]
            results_by_id = {}
            failures = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                requests_by_genre = {
                    executor.submit(movies_by_genre, genre["id"]): genre
                    for genre in shelf_genres
                }
                for pending in as_completed(requests_by_genre):
                    genre = requests_by_genre[pending]
                    try:
                        results_by_id[genre["id"]] = pending.result()
                    except TMDBError as error:
                        failures.append(str(error))
            genre_rows = [
                {"name": genre["name"], "movies": results_by_id[genre["id"]]}
                for genre in shelf_genres
                if results_by_id.get(genre["id"])
            ]
            if failures:
                tmdb_error = (
                    "Some movie genres could not be loaded from TMDB."
                    if genre_rows
                    else failures[0]
                )
    except TMDBError as error:
        tmdb_error = str(error)

    return render_template(
        "genres.html",
        genres=genre_list,
        selected_genre=selected_genre,
        movies=movies,
        genre_rows=genre_rows,
        tmdb_error=tmdb_error,
    )


@main.route("/movie/<int:movie_id>")
def movie(movie_id):
    if request.args.get("local") == "1":
        local_movie = db.session.get(Movie, movie_id)
        if local_movie is None:
            abort(404)
        return _render_movie_page(_local_movie_details(local_movie))

    if request.args.get("source") != "tmdb":
        # Keep old /movie/<local database id> links working.
        local_movie = db.session.get(Movie, movie_id)
        if local_movie is not None:
            return _render_movie_page(_local_movie_details(local_movie))

    try:
        data = movie_details(movie_id)
        data["poster_url"] = _cache_movie_posters([data])[0].get("poster_url")
        data["movie_key"] = f"tmdb:{movie_id}"
        _write_tmdb_cache(f"movie:{movie_id}", data)
        return _render_movie_page(data)
    except TMDBError as error:
        data = _read_tmdb_cache(f"movie:{movie_id}")
        if data:
            data["movie_key"] = f"tmdb:{movie_id}"
            data.setdefault("cast", [])
            data.setdefault("directors", [])
            data.setdefault("crew", [])
            data.setdefault("trailer_url", None)
            return _render_movie_page(data, tmdb_error=f"TMDB is unavailable. Showing saved movie information; cast and crew may be incomplete. {error}")
        return render_template("movie.html", movie=None, tmdb_error=str(error)), 503


@main.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.account"))
    form = RegistrationForm()
    if form.validate_on_submit():
        username = form.username.data.strip().casefold()
        email = form.email.data.strip().casefold()
        existing = User.query.filter(
            (func.lower(User.username) == username.casefold()) | (func.lower(User.email) == email)
        ).first()
        if existing:
            flash("That username or email is already registered.", "error")
        else:
            user = User(username=username, email=email)
            user.set_password(form.password.data)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("That username or email is already registered.", "error")
            else:
                login_user(user)
                flash("Your account is ready.", "success")
                return redirect(url_for("main.account"))
    return render_template("register.html", form=form)


@main.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.account"))
    form = LoginForm()
    if form.validate_on_submit():
        identity = form.identifier.data.strip().casefold()
        user = User.query.filter(
            (func.lower(User.username) == identity) | (func.lower(User.email) == identity)
        ).first()
        if user is None or not user.check_password(form.password.data):
            flash("The username/email or password is incorrect.", "error")
        else:
            login_user(user)
            flash(f"Welcome back, {user.username}.", "success")
            next_url = request.args.get("next", "")
            parsed_next = urlsplit(next_url)
            if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url and not parsed_next.scheme and not parsed_next.netloc:
                return redirect(next_url)
            return redirect(url_for("main.account"))
    return render_template("login.html", form=form)


@main.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You have signed out.", "success")
    return redirect(url_for("main.home"))


@main.get("/account")
@login_required
def account():
    reviews = Review.query.filter_by(user_id=current_user.id).order_by(Review.updated_at.desc()).all()
    return render_template("account.html", reviews=reviews)


@main.get("/favorites")
@login_required
def favorites():
    saved_movies = Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.created_at.desc()).all()
    return render_template("favorites.html", favorites=saved_movies)


@main.post("/movie/<int:movie_id>/favorite")
@login_required
def toggle_favorite(movie_id):
    is_local = request.args.get("local") == "1"
    if not is_local and request.args.get("source") != "tmdb":
        abort(400)

    movie_key = f"local:{movie_id}" if is_local else f"tmdb:{movie_id}"
    favorite = Favorite.query.filter_by(user_id=current_user.id, movie_key=movie_key).first()
    if favorite:
        db.session.delete(favorite)
        db.session.commit()
        flash("Movie removed from your favorites.", "success")
    else:
        if is_local:
            local_movie = db.session.get(Movie, movie_id)
            if local_movie is None:
                abort(404)
            data = _local_movie_details(local_movie)
            title = data["title"]
            poster_url = data["poster_url"]
            release_date = data["release_date"]
        else:
            title = request.form.get("movie_title", "").strip()[:200]
            if not title:
                flash("Open the movie details page and try saving it again.", "error")
                return redirect(_movie_url(movie_id, False))
            poster_url = _validated_tmdb_poster(request.form.get("poster_url", ""))
            release_date = request.form.get("release_date", "").strip()[:32]

        db.session.add(Favorite(
            user_id=current_user.id,
            movie_key=movie_key,
            movie_title=title,
            poster_url=poster_url,
            release_date=release_date,
        ))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("This movie is already in your favorites.", "info")
        else:
            flash("Movie added to your favorites.", "success")

    return redirect(_movie_url(movie_id, is_local))


@main.post("/movie/<int:movie_id>/review")
@login_required
def submit_review(movie_id):
    is_local = request.args.get("local") == "1"
    if is_local:
        local_movie = db.session.get(Movie, movie_id)
        if local_movie is None:
            abort(404)
        data = _local_movie_details(local_movie)
    elif request.args.get("source") == "tmdb":
        data = {
            "movie_key": f"tmdb:{movie_id}",
            "title": request.form.get("movie_title", f"TMDB movie {movie_id}").strip()[:200],
        }
    else:
        local_movie = db.session.get(Movie, movie_id)
        if local_movie is None:
            abort(404)
        is_local = True
        data = _local_movie_details(local_movie)

    form = ReviewForm()
    if not form.validate_on_submit():
        errors = [message for messages in form.errors.values() for message in messages]
        flash(errors[0] if errors else "Please check your rating and try again.", "error")
    else:
        review = Review.query.filter_by(user_id=current_user.id, movie_key=data["movie_key"]).first()
        if review is None:
            review = Review(user_id=current_user.id, movie_key=data["movie_key"])
            db.session.add(review)
        review.movie_title = data.get("title", f"Movie {movie_id}")
        review.rating = form.rating.data
        review.review_text = (form.review_text.data or "").strip() or None
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Your rating could not be saved. Please try again.", "error")
        else:
            flash("Your rating and review have been saved.", "success")

    return redirect(_movie_url(movie_id, is_local) + "#reviews")


def _render_movie_page(data, tmdb_error=None, status=200):
    movie_key = data["movie_key"]
    reviews = Review.query.filter_by(movie_key=movie_key).order_by(Review.updated_at.desc()).all()
    average_rating = db.session.query(func.avg(Review.rating)).filter_by(movie_key=movie_key).scalar()
    my_review = None
    is_favorite = False
    if current_user.is_authenticated:
        my_review = Review.query.filter_by(movie_key=movie_key, user_id=current_user.id).first()
        is_favorite = Favorite.query.filter_by(movie_key=movie_key, user_id=current_user.id).first() is not None
    form = ReviewForm(data={"rating": my_review.rating, "review_text": my_review.review_text or ""}) if my_review else ReviewForm()
    return render_template(
        "movie.html",
        movie=data,
        reviews=reviews,
        average_rating=average_rating,
        review_form=form,
        my_review=my_review,
        is_favorite=is_favorite,
        tmdb_error=tmdb_error,
    ), status


def _validated_tmdb_poster(poster_url):
    candidate = (poster_url or "").strip()[:500]
    parsed = urlsplit(candidate)
    if parsed.scheme == "https" and parsed.netloc == "image.tmdb.org" and parsed.path.startswith("/t/p/"):
        return candidate
    if re.fullmatch(r"/static/images/tmdb-cache/movie-\d+\.jpg", candidate):
        return candidate
    return None


def _movie_url(movie_id, is_local):
    if is_local:
        return url_for("main.movie", movie_id=movie_id, local=1)
    return url_for("main.movie", movie_id=movie_id, source="tmdb")


def _read_tmdb_cache(cache_key, default=None):
    entry = db.session.get(TMDBCacheEntry, cache_key)
    return entry.payload if entry is not None else default


def _cache_movie_posters(movies):
    """Save a few real TMDB posters locally so cached movies remain visual offline."""
    saved_movies = [dict(movie) for movie in movies]
    if current_app.testing:
        return saved_movies

    poster_directory = Path(current_app.static_folder) / "images" / "tmdb-cache"
    downloads = []
    for movie in saved_movies[:8]:
        movie_id = movie.get("id")
        poster_url = movie.get("poster_url")
        if not isinstance(movie_id, int) or not poster_url:
            continue
        filename = f"movie-{movie_id}.jpg"
        poster_path = poster_directory / filename
        if poster_path.is_file():
            movie["poster_url"] = url_for("static", filename=f"images/tmdb-cache/{filename}")
            continue
        parsed = urlsplit(poster_url)
        if parsed.scheme == "https" and parsed.netloc == "image.tmdb.org" and parsed.path.startswith("/t/p/"):
            downloads.append((movie, poster_url, poster_path, filename))

    def download_poster(item):
        _, poster_url, poster_path, filename = item
        try:
            with requests.get(poster_url, timeout=(2, 4), stream=True) as response:
                response.raise_for_status()
                if urlsplit(response.url).netloc != "image.tmdb.org":
                    return None
                if not response.headers.get("content-type", "").startswith("image/"):
                    return None
                body = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        return None
            if not body:
                return None
            poster_path.parent.mkdir(parents=True, exist_ok=True)
            poster_path.write_bytes(body)
            return filename
        except (requests.RequestException, OSError):
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = [executor.submit(download_poster, item) for item in downloads]
        for future in as_completed(results):
            filename = future.result()
            if filename:
                movie = next(item[0] for item in downloads if item[3] == filename)
                movie["poster_url"] = url_for("static", filename=f"images/tmdb-cache/{filename}")
    return saved_movies


def _write_tmdb_cache(cache_key, payload):
    """Save public TMDB data without ever storing credentials."""
    try:
        entry = db.session.get(TMDBCacheEntry, cache_key)
        if entry is None:
            entry = TMDBCacheEntry(cache_key=cache_key, payload=payload)
            db.session.add(entry)
        else:
            entry.payload = payload
        entry.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _cache_tmdb_movies(cache_key, movies):
    """Keep a bounded local copy of recent real TMDB results for offline fallback."""
    now = datetime.now(timezone.utc)
    try:
        entry = db.session.get(TMDBCacheEntry, cache_key)
        if entry is None:
            entry = TMDBCacheEntry(cache_key=cache_key, payload=movies, updated_at=now)
            db.session.add(entry)
        else:
            entry.payload = movies
            entry.updated_at = now

        for movie_data in movies:
            movie_id = movie_data.get("id")
            if movie_id is None:
                continue
            movie_key = f"movie:{movie_id}"
            cached = db.session.get(TMDBCacheEntry, movie_key)
            # Preserve credits from a previously fetched detail page when a list is refreshed.
            payload = {**(cached.payload if cached and isinstance(cached.payload, dict) else {}), **movie_data}
            if cached is None:
                db.session.add(TMDBCacheEntry(cache_key=movie_key, payload=payload, updated_at=now))
            else:
                cached.payload = payload
                cached.updated_at = now

        db.session.flush()
        old_searches = (
            TMDBCacheEntry.query.filter(TMDBCacheEntry.cache_key.like("search:%"))
            .order_by(TMDBCacheEntry.updated_at.desc())
            .offset(10)
            .all()
        )
        for old_entry in old_searches:
            db.session.delete(old_entry)
        old_movies = (
            TMDBCacheEntry.query.filter(TMDBCacheEntry.cache_key.like("movie:%"))
            .order_by(TMDBCacheEntry.updated_at.desc())
            .offset(100)
            .all()
        )
        for old_entry in old_movies:
            db.session.delete(old_entry)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _cached_tmdb_movie_pool():
    entries = (
        TMDBCacheEntry.query.filter(TMDBCacheEntry.cache_key.like("movie:%"))
        .order_by(TMDBCacheEntry.updated_at.desc())
        .all()
    )
    movies = []
    seen_ids = set()
    for entry in entries:
        if not isinstance(entry.payload, dict):
            continue
        movie_data = dict(entry.payload)
        movie_id = movie_data.get("id")
        if movie_id is None or movie_id in seen_ids:
            continue
        seen_ids.add(movie_id)
        movies.append(movie_data)
    return movies


def _local_movies():
    return [
        {
            "id": movie.id,
            "title": movie.title,
            "poster_url": url_for("static", filename=movie.poster) if movie.poster else None,
            "genres": (movie.genre or "").split(),
            "rating": None,  # Legacy imdb_rating is not presented as a TMDB rating.
            "release_date": str(movie.release_year or ""),
            "release_year": movie.release_year,
            "overview": movie.description or "No description is available.",
            "is_local": True,
        }
        for movie in Movie.query.order_by(Movie.title).all()
    ]


def _local_movie_details(movie):
    cast = Cast.query.filter_by(movie_id=movie.id).order_by(Cast.id).all()
    crew = Crew.query.filter_by(movie_id=movie.id).order_by(Crew.id).all()
    return {
        "id": movie.id,
        "movie_key": f"local:{movie.id}",
        "title": movie.title,
        "poster_url": url_for("static", filename=movie.poster) if movie.poster else None,
        "genres": (movie.genre or "").split(),
        "rating": None,
        "release_date": str(movie.release_year or ""),
        "release_year": movie.release_year,
        "runtime": movie.duration,
        "overview": movie.description or "No description is available.",
        "cast": [
            {"name": person.actor_name, "character": person.character_name, "photo_url": url_for("static", filename=person.photo) if person.photo else None}
            for person in cast
        ],
        "directors": [person.name for person in crew if person.role and person.role.casefold() == "director"],
        "crew": [
            {"name": person.name, "job": person.role or "Crew", "department": "", "photo_url": url_for("static", filename=person.photo) if person.photo else None}
            for person in crew
        ],
        "trailer_url": movie.trailer,
        "is_local": True,
    }
