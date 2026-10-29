from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import db
from app.forms import LoginForm, RegistrationForm, ReviewForm
from app.models import Cast, Crew, Movie, Review, User
from app.tmdb import TMDBError, movie_details, movie_genres, movies_by_genre, popular_movies, search_movies

main = Blueprint("main", __name__)


@main.route("/")
def home():
    try:
        movies = popular_movies()
        tmdb_error = None
    except TMDBError as error:
        movies = _local_movies()
        tmdb_error = str(error)
    return render_template(
        "home.html",
        movies=movies,
        featured_movie=movies[0] if movies else None,
        tmdb_error=tmdb_error,
    )


@main.route("/search")
def search():
    query = request.args.get("q", "").strip()[:100]
    if not query:
        return render_template("home.html", movies=[], featured_movie=None, query=query, tmdb_error="Enter a movie title to search.")
    try:
        movies = search_movies(query)
        tmdb_error = None
    except TMDBError as error:
        movies = [movie for movie in _local_movies() if query.casefold() in movie["title"].casefold()]
        tmdb_error = str(error)
    return render_template(
        "home.html",
        movies=movies,
        featured_movie=movies[0] if movies else None,
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
        data["movie_key"] = f"tmdb:{movie_id}"
        return _render_movie_page(data)
    except TMDBError as error:
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
    if current_user.is_authenticated:
        my_review = Review.query.filter_by(movie_key=movie_key, user_id=current_user.id).first()
    form = ReviewForm(data={"rating": my_review.rating, "review_text": my_review.review_text or ""}) if my_review else ReviewForm()
    return render_template(
        "movie.html",
        movie=data,
        reviews=reviews,
        average_rating=average_rating,
        review_form=form,
        my_review=my_review,
        tmdb_error=tmdb_error,
    ), status


def _movie_url(movie_id, is_local):
    if is_local:
        return url_for("main.movie", movie_id=movie_id, local=1)
    return url_for("main.movie", movie_id=movie_id, source="tmdb")


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
