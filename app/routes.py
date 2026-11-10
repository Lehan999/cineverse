from datetime import datetime, timezone
import re
import secrets
import random
import csv
import io
import uuid
import hashlib
import os
from collections import Counter
from datetime import date
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app import db, tmdb
from app.forms import LoginForm, RegistrationForm, ReviewForm
from app.models import (Cast, CatalogMovie, CineRoom, CineRoomVote, Crew, DuelPreference,
    Favorite, ImportPreview, Movie, Review, ReviewHistory, ReviewReport, ReviewVote,
    AuthThrottle, TMDBCacheEntry, User, UserCollection, UserCollectionItem, WatchEntry)
from app.catalog import as_card, find_local_movies, normalize_title, resolve_catalog_title, safe_artwork_url

main = Blueprint("main", __name__)


@main.route("/")
def home():
    local_rows = find_local_movies()
    movies = [as_card(row) for row in local_rows]
    featured_movies = movies[:1]
    return render_template(
        "home.html",
        movies=movies,
        featured_movies=featured_movies,
        featured_notice=None,
        tmdb_error=None,
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
        return redirect(url_for("main.home"))

    movies = []
    tmdb_error = None
    search_notice = None
    search_input_error = not normalize_title(query)

    if not search_input_error:
        movies = [as_card(row) for row in find_local_movies(query)]
        if not movies:
            cache_key = f"search:{normalize_title(query)}"
            cached_results = _read_tmdb_cache(cache_key)
            try:
                provider_results = tmdb.search_movies(query)
            except tmdb.TMDBError:
                provider_results = cached_results if isinstance(cached_results, list) else []
                if provider_results:
                    search_notice = "TMDb is unavailable right now. Showing your most recent saved search results."
                else:
                    tmdb_error = "Online movie search is temporarily unavailable. Please try again shortly."
            else:
                if provider_results:
                    # Save only user-requested results, plus the existing bounded detail cache
                    # needed by the movie links. Homepage and genre rendering stay offline.
                    _cache_tmdb_movies(cache_key, provider_results)

            movies = [
                {
                    **movie,
                    "poster_url": safe_artwork_url(movie.get("poster_url")),
                    "source": "TMDb",
                    "rating_source": "TMDb",
                }
                for movie in provider_results
            ]

    return render_template(
        "home.html",
        movies=movies,
        featured_movies=[],
        query=query,
        tmdb_error=tmdb_error,
        search_notice=search_notice,
        search_input_error=search_input_error,
    )


@main.get("/genres")
def genres():
    selected_genre = request.args.get("genre", "").strip() or None
    def genre_catalog(genre=None):
        return [row for row in find_local_movies(genre=genre) if row.title.strip().casefold() != "avengers endgame"]

    all_movies = [as_card(row) for row in genre_catalog(selected_genre)]
    genre_names = sorted({genre for movie in CatalogMovie.query.all() for genre in (movie.genres_json or [])})
    genre_list = [{"id": name, "name": name} for name in genre_names]
    genre_rows = [{"name": selected_genre, "movies": all_movies}] if selected_genre else [
        {"name": name, "movies": [as_card(row) for row in genre_catalog(name)]}
        for name in genre_names if genre_catalog(name)
    ]
    movies = all_movies

    return render_template(
        "genres.html",
        genres=genre_list,
        selected_genre=selected_genre,
        movies=movies,
        genre_rows=genre_rows,
        tmdb_error=None,
    )


def _catalog_detail(record):
    data = as_card(record)
    data.update({
        "movie_key": f"catalog:{record.id}", "runtime": record.runtime_minutes,
        "cast": [{**person, "photo_url": safe_artwork_url(person.get("photo_url"))}
                 for person in (record.cast_json or []) if isinstance(person, dict)],
        "crew": [{**person, "photo_url": safe_artwork_url(person.get("photo_url"))}
                 for person in (record.crew_json or []) if isinstance(person, dict)], "directors": [],
        "trailer_url": None, "is_catalog": True,
        "rating_sources": record.ratings_json or {},
    })
    data["directors"] = [person.get("name") for person in data["crew"] if person.get("job", "").casefold() == "director"]
    trailer = record.provenance_json.get("trailer_url") if isinstance(record.provenance_json, dict) else None
    parsed_trailer = urlsplit(trailer or "")
    if parsed_trailer.scheme == "https" and parsed_trailer.hostname in {"youtube.com", "www.youtube.com", "youtu.be"}:
        data["trailer_url"] = trailer
    return data


@main.get("/film/<int:movie_id>")
def catalog_movie(movie_id):
    record = db.session.get(CatalogMovie, movie_id)
    if record is None:
        abort(404)
    return _render_movie_page(_catalog_detail(record))


@main.post("/film/<int:movie_id>/favorite")
@login_required
def catalog_favorite(movie_id):
    record = db.session.get(CatalogMovie, movie_id)
    if record is None:
        abort(404)
    key = f"catalog:{record.id}"
    favorite = Favorite.query.filter_by(user_id=current_user.id, movie_key=key).first()
    if favorite:
        db.session.delete(favorite)
    else:
        db.session.add(Favorite(user_id=current_user.id, movie_key=key, catalog_movie_id=record.id, movie_title=record.title,
                                poster_url=record.poster_url, release_date=record.release_date or str(record.year or "")))
    db.session.commit()
    return redirect(url_for("main.catalog_movie", movie_id=record.id))


def _spoiler_blocks(text_value):
    blocks = []
    for paragraph in (text_value or "").splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        pieces = re.split(r"\[\[spoiler\]\](.*?)\[\[/spoiler\]\]", paragraph, flags=re.I)
        for index, piece in enumerate(pieces):
            if piece.strip():
                blocks.append({"text": piece.strip(), "spoiler": index % 2 == 1})
    return blocks


@main.post("/film/<int:movie_id>/review")
@login_required
def catalog_review(movie_id):
    record = db.session.get(CatalogMovie, movie_id)
    if record is None:
        abort(404)
    form = ReviewForm()
    if form.validate_on_submit():
        key = f"catalog:{record.id}"
        review = Review.query.filter_by(user_id=current_user.id, movie_key=key).first()
        if review is None:
            review = Review(user_id=current_user.id, movie_key=key, catalog_movie_id=record.id, movie_title=record.title)
            db.session.add(review)
        else:
            db.session.add(ReviewHistory(review_id=review.id, rating=review.rating, review_text=review.review_text,
                                         spoiler_paragraphs_json=review.spoiler_paragraphs_json or []))
        review.movie_title = record.title
        review.rating = form.rating.data
        review.review_text = (form.review_text.data or "").strip() or None
        review.spoiler_paragraphs_json = _spoiler_blocks(review.review_text)
        db.session.commit()
    else:
        flash("Please enter a valid rating from 1 to 10.", "error")
    return redirect(url_for("main.catalog_movie", movie_id=record.id) + "#reviews")


@main.post("/reviews/<int:review_id>/helpful")
@login_required
def review_helpful(review_id):
    review = db.session.get(Review, review_id)
    if review is None or review.user_id == current_user.id:
        abort(404)
    existing = ReviewVote.query.filter_by(user_id=current_user.id, review_id=review_id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(ReviewVote(user_id=current_user.id, review_id=review_id))
    db.session.commit()
    return redirect(_review_page_url(review))


@main.post("/reviews/<int:review_id>/report")
@login_required
def report_review(review_id):
    review = db.session.get(Review, review_id)
    reason = request.form.get("reason", "").strip()[:240]
    if review is None or review.user_id == current_user.id or not reason:
        abort(400)
    if not ReviewReport.query.filter_by(user_id=current_user.id, review_id=review_id).first():
        db.session.add(ReviewReport(user_id=current_user.id, review_id=review_id, reason=reason))
        db.session.commit()
    flash("Thank you. Your report was recorded for review.", "info")
    return redirect(_review_page_url(review))


def _review_page_url(review):
    if review.catalog_movie_id:
        return url_for("main.catalog_movie", movie_id=review.catalog_movie_id) + "#reviews"
    prefix, _, raw_id = review.movie_key.partition(":")
    if raw_id.isdigit():
        return _movie_url(int(raw_id), prefix == "local") + "#reviews"
    return url_for("main.home")


@main.route("/moderation", methods=["GET", "POST"])
@login_required
def moderation_queue():
    moderator_ids = {int(value.strip()) for value in os.getenv("CINEVERSE_MODERATOR_IDS", "").split(",") if value.strip().isdigit()}
    if current_user.id not in moderator_ids:
        abort(403)
    if request.method == "POST":
        report = db.session.get(ReviewReport, request.form.get("report_id", type=int))
        if report is None:
            abort(404)
        review = db.session.get(Review, report.review_id)
        action = request.form.get("action")
        if action == "hide":
            review.is_hidden = True
        elif action == "restore":
            review.is_hidden = False
        else:
            abort(400)
        report.status = "reviewed"
        db.session.commit()
        flash("Moderation action saved.", "success")
        return redirect(url_for("main.moderation_queue"))
    reports = ReviewReport.query.order_by(ReviewReport.created_at.desc()).all()
    return render_template("moderation.html", reports=reports)


@main.post("/film/<int:movie_id>/watch")
@login_required
def watch_movie(movie_id):
    record = db.session.get(CatalogMovie, movie_id)
    if record is None:
        abort(404)
    status = request.form.get("status", "watchlist")
    if status not in {"watchlist", "watched"}:
        abort(400)
    entry = WatchEntry.query.filter_by(user_id=current_user.id, movie_id=movie_id).first()
    if entry is None:
        entry = WatchEntry(user_id=current_user.id, movie_id=movie_id)
        db.session.add(entry)
    entry.status = status
    entry.private_note = request.form.get("private_note", "").strip()[:2000] or None
    entry.watched_on = datetime.now().date() if status == "watched" else None
    db.session.commit()
    return redirect(url_for("main.catalog_movie", movie_id=movie_id))


@main.route("/cine-match", methods=["GET", "POST"])
def cine_match():
    mood = request.values.get("mood", "").strip().casefold()
    language = request.values.get("language", "").strip().casefold()
    genre = request.values.get("genre", "").strip()
    runtime = request.values.get("time", "any")
    watched_ids = {row.movie_id for row in WatchEntry.query.filter_by(user_id=current_user.id).all()} if current_user.is_authenticated else set()
    candidates = CatalogMovie.query.all()
    ranked = []
    for movie in candidates:
        tags = [str(value).casefold() for value in (movie.mood_tags_json or [])]
        genres = [str(value).casefold() for value in (movie.genres_json or [])]
        if mood and mood not in tags:
            continue
        if genre and genre.casefold() not in genres:
            continue
        if language and language != "any" and language != (movie.language or "").casefold():
            continue
        if runtime == "short" and movie.runtime_minutes and movie.runtime_minutes > 100:
            continue
        if runtime == "medium" and movie.runtime_minutes and not 90 <= movie.runtime_minutes <= 150:
            continue
        if runtime == "long" and movie.runtime_minutes and movie.runtime_minutes < 140:
            continue
        score = (4 if mood and mood in tags else 0) + (3 if genre and genre.casefold() in genres else 0)
        score += 2 if language and language == (movie.language or "").casefold() else 0
        score += 1 if movie.id not in watched_ids else -3
        ranked.append((score, movie))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].title.casefold()))
    results = [{"movie": as_card(movie), "why": "Matches your chosen " + ", ".join(x for x in (mood or None, genre or None, language if language not in ("", "any") else None) if x)} for score, movie in ranked[:3]] if request.method == "POST" else []
    return render_template("cine_match.html", results=results, mood=mood, language=language, genre=genre, genres=sorted({g for item in candidates for g in (item.genres_json or [])}))


@main.get("/roulette")
def roulette():
    movies = CatalogMovie.query.all()
    watched = {row.movie_id for row in WatchEntry.query.filter_by(user_id=current_user.id, status="watched").all()} if current_user.is_authenticated else set()
    pool = [item for item in movies if item.id not in watched] or movies
    selected = random.choice(pool) if pool else None
    return render_template("roulette.html", movie=as_card(selected) if selected else None)


@main.route("/cine-duel", methods=["GET", "POST"])
@login_required
def cine_duel():
    movies = CatalogMovie.query.order_by(CatalogMovie.title).all()
    if request.method == "POST":
        try:
            winner_id, loser_id = int(request.form.get("winner")), int(request.form.get("loser"))
        except (TypeError, ValueError):
            abort(400)
        if winner_id == loser_id or not db.session.get(CatalogMovie, winner_id) or not db.session.get(CatalogMovie, loser_id):
            abort(400)
        db.session.add(DuelPreference(user_id=current_user.id, winner_movie_id=winner_id, loser_movie_id=loser_id))
        db.session.commit()
        flash("Preference saved. Your future CineMatch suggestions can use it.", "success")
        return redirect(url_for("main.cine_duel"))
    return render_template("cine_duel.html", pair=movies[:2] if len(movies) >= 2 else [])


@main.get("/cinema-dna")
@login_required
def cinema_dna():
    watches = WatchEntry.query.filter_by(user_id=current_user.id).all()
    reviews = Review.query.filter_by(user_id=current_user.id).all()
    rated_keys = {review.movie_key for review in reviews}
    ids = {entry.movie_id for entry in watches}
    ids |= {int(review.movie_key.split(":", 1)[1]) for review in reviews if review.movie_key.startswith("catalog:") and review.movie_key.split(":", 1)[1].isdigit()}
    films = CatalogMovie.query.filter(CatalogMovie.id.in_(ids)).all() if ids else []
    genres = Counter(g for movie in films for g in (movie.genres_json or []))
    languages = Counter(movie.language for movie in films if movie.language)
    return render_template("cinema_dna.html", genres=genres.most_common(), languages=languages.most_common(), reviews=reviews,
                           average=sum(review.rating for review in reviews) / len(reviews) if reviews else None,
                           watched=sum(1 for entry in watches if entry.status == "watched"), saved=sum(1 for entry in watches if entry.status == "watchlist"),
                           has_activity=bool(watches or reviews or rated_keys))


@main.route("/cine-room", methods=["GET", "POST"])
@login_required
def cine_room_create():
    if request.method == "POST":
        room = CineRoom(code=secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper(), owner_id=current_user.id)
        db.session.add(room)
        db.session.commit()
        return redirect(url_for("main.cine_room", code=room.code))
    return render_template("cine_room_create.html")


@main.route("/cine-room/<string:code>", methods=["GET", "POST"])
def cine_room(code):
    room = CineRoom.query.filter_by(code=code.upper()).first_or_404()
    token_key = f"room:{room.code}"
    if token_key not in session:
        session[token_key] = secrets.token_urlsafe(24)
    if request.method == "POST":
        if request.form.get("action") == "reveal":
            if not current_user.is_authenticated or current_user.id != room.owner_id:
                abort(403)
            room.is_revealed = True
            db.session.commit()
            return redirect(url_for("main.cine_room", code=room.code))
        try:
            movie_id = int(request.form.get("movie_id"))
        except (TypeError, ValueError):
            abort(400)
        if db.session.get(CatalogMovie, movie_id) is None:
            abort(400)
        vote = CineRoomVote.query.filter_by(room_id=room.id, voter_token=session[token_key]).first()
        if vote is None:
            db.session.add(CineRoomVote(room_id=room.id, voter_token=session[token_key], movie_id=movie_id))
        else:
            vote.movie_id = movie_id
        db.session.commit()
        flash("Your vote is private and saved.", "success")
    counts = db.session.query(CineRoomVote.movie_id, func.count(CineRoomVote.id)).filter_by(room_id=room.id).group_by(CineRoomVote.movie_id).all()
    result_ids = [movie_id for movie_id, count in counts if count == max([value for _, value in counts], default=0)] if counts else []
    result = CatalogMovie.query.filter(CatalogMovie.id.in_(result_ids)).all() if result_ids and room.is_revealed else []
    return render_template("cine_room.html", room=room, movies=CatalogMovie.query.order_by(CatalogMovie.title).all(), result=result,
                           vote_count=sum(count for _, count in counts) if room.is_revealed else None,
                           voted=bool(CineRoomVote.query.filter_by(room_id=room.id, voter_token=session[token_key]).first()),
                           is_room_owner=current_user.is_authenticated and current_user.id == room.owner_id)


@main.route("/lists", methods=["GET", "POST"])
@login_required
def user_lists():
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:80]
        description = request.form.get("description", "").strip()[:300]
        if not title:
            flash("List name is required.", "error")
        elif UserCollection.query.filter_by(user_id=current_user.id, title=title).first():
            flash("You already have a list with that name.", "error")
        else:
            db.session.add(UserCollection(user_id=current_user.id, title=title, description=description or None))
            db.session.commit()
            flash("List created.", "success")
            return redirect(url_for("main.user_lists"))
    collections = UserCollection.query.filter_by(user_id=current_user.id).order_by(UserCollection.created_at.desc()).all()
    return render_template("lists.html", collections=collections)


@main.post("/lists/<int:list_id>/add/<int:movie_id>")
@login_required
def add_to_list(list_id, movie_id):
    collection = UserCollection.query.filter_by(id=list_id, user_id=current_user.id).first_or_404()
    if db.session.get(CatalogMovie, movie_id) is None:
        abort(404)
    if not UserCollectionItem.query.filter_by(collection_id=list_id, movie_id=movie_id).first():
        db.session.add(UserCollectionItem(collection_id=list_id, movie_id=movie_id))
        db.session.commit()
    return redirect(url_for("main.user_lists"))


@main.get("/data-export.csv")
@login_required
def data_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["movie_key", "title", "rating", "review", "watch_status", "watched_on", "private_note"])
    for review in Review.query.filter_by(user_id=current_user.id).all():
        entry = WatchEntry.query.filter_by(user_id=current_user.id, movie_id=int(review.movie_key.split(":", 1)[1])).first() if review.movie_key.startswith("catalog:") and review.movie_key.split(":", 1)[1].isdigit() else None
        writer.writerow([review.movie_key, review.movie_title, review.rating, review.review_text or "", entry.status if entry else "", entry.watched_on.isoformat() if entry and entry.watched_on else "", entry.private_note if entry else ""])
    response = current_app.response_class(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=cineverse-personal-data.csv"
    return response


@main.route("/data-import", methods=["GET", "POST"])
@login_required
def data_import():
    preview = None
    if request.method == "POST" and request.form.get("undo_id"):
        preview = ImportPreview.query.filter_by(id=request.form["undo_id"], user_id=current_user.id).first_or_404()
        for entry_id in preview.imported_watch_ids_json or []:
            entry = WatchEntry.query.filter_by(id=entry_id, user_id=current_user.id).first()
            if entry:
                db.session.delete(entry)
        preview.imported_watch_ids_json = []
        db.session.commit()
        flash("Imported entries were undone.", "success")
        return redirect(url_for("main.data_import"))
    if request.method == "POST" and request.form.get("preview_id"):
        preview = ImportPreview.query.filter_by(id=request.form["preview_id"], user_id=current_user.id).first_or_404()
        if request.form.get("confirm") == "yes":
            inserted = []
            for row in preview.rows_json:
                if row.get("status") != "matched":
                    continue
                movie_id = row["movie_id"]
                if WatchEntry.query.filter_by(user_id=current_user.id, movie_id=movie_id).first():
                    continue
                entry = WatchEntry(user_id=current_user.id, movie_id=movie_id, status=row.get("status_import", "watchlist"))
                db.session.add(entry)
                db.session.flush()
                inserted.append(entry.id)
            preview.imported_watch_ids_json = inserted
            db.session.commit()
            flash(f"Imported {len(inserted)} new watchlist entries. You can undo this import.", "success")
            return redirect(url_for("main.data_import"))
    elif request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename.casefold().endswith(".csv"):
            flash("Choose a CSV file.", "error")
        else:
            try:
                text_value = upload.stream.read(1024 * 1024 + 1).decode("utf-8-sig")
                if len(text_value) > 1024 * 1024:
                    raise ValueError("File is over the 1 MB limit.")
                reader = csv.DictReader(io.StringIO(text_value))
                if not reader.fieldnames or not any(name and name.casefold() == "title" for name in reader.fieldnames):
                    raise ValueError("CSV needs a title column.")
                rows = []
                catalog = CatalogMovie.query.all()
                for index, source_row in enumerate(reader):
                    if index >= 100:
                        raise ValueError("Only the first 100 rows can be imported at a time.")
                    source_row = {str(key).casefold().strip(): (value or "").strip() for key, value in source_row.items() if key}
                    title = source_row.get("title", "")[:240]
                    year_value = source_row.get("year", "")[:4]
                    match_status, matched_movie = resolve_catalog_title(catalog, title, year_value or None)
                    if match_status == "matched" and WatchEntry.query.filter_by(user_id=current_user.id, movie_id=matched_movie.id).first():
                        match_status = "duplicate"
                    rows.append({"row": index + 2, "title": title, "year": year_value,
                                 "status": match_status,
                                 "movie_id": matched_movie.id if match_status == "matched" and matched_movie else None,
                                 "status_import": "watched" if source_row.get("watched", "").casefold() in ("1", "true", "yes", "watched") else "watchlist"})
                preview = ImportPreview(id=str(uuid.uuid4()), user_id=current_user.id, rows_json=rows)
                db.session.add(preview)
                db.session.commit()
            except (UnicodeDecodeError, csv.Error, ValueError) as error:
                db.session.rollback()
                flash(str(error)[:200], "error")
    pending = ImportPreview.query.filter_by(user_id=current_user.id).order_by(ImportPreview.created_at.desc()).first()
    if preview is None:
        preview = pending
    return render_template("data_import.html", preview=preview)


@main.route("/movie/<int:movie_id>")
def movie(movie_id):
    if request.args.get("source") == "catalog":
        return redirect(url_for("main.catalog_movie", movie_id=movie_id))
    if request.args.get("source") == "tmdb":
        legacy_catalog = CatalogMovie.query.filter_by(source="tmdb-legacy", external_id=str(movie_id)).first()
        if legacy_catalog:
            return _render_movie_page(_catalog_detail(legacy_catalog))
        data = _read_tmdb_cache(f"movie:{movie_id}")
        if data:
            data["poster_url"] = safe_artwork_url(data.get("poster_url"))
            for person_list in (data.get("cast", []), data.get("crew", [])):
                for person in person_list:
                    if isinstance(person, dict):
                        person["photo_url"] = safe_artwork_url(person.get("photo_url"))
            trailer = urlsplit(data.get("trailer_url") or "")
            if not (trailer.scheme == "https" and trailer.hostname in {"youtube.com", "www.youtube.com", "youtu.be"}):
                data["trailer_url"] = None
            data["movie_key"] = f"tmdb:{movie_id}"
            data.setdefault("cast", [])
            data.setdefault("directors", [])
            data.setdefault("crew", [])
            data.setdefault("trailer_url", None)
            return _render_movie_page(data, tmdb_error="Showing preserved movie information from the legacy cache.")
        return render_template("movie.html", movie=None, tmdb_error="This legacy movie was not saved locally. Provider access is disabled on this page."), 404
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

    return render_template("movie.html", movie=None, tmdb_error="This movie is not in the local catalog."), 404


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
        bucket = hashlib.sha256(f"{current_app.config['SECRET_KEY']}|{request.remote_addr or 'unknown'}".encode()).hexdigest()
        throttle = db.session.get(AuthThrottle, bucket)
        now = datetime.now(timezone.utc)
        if throttle and (now - (throttle.window_started.replace(tzinfo=timezone.utc) if throttle.window_started.tzinfo is None else throttle.window_started)).total_seconds() < 900 and throttle.attempts >= 8:
            flash("Too many sign-in attempts. Wait 15 minutes and try again.", "error")
            return render_template("login.html", form=form), 429
        user = User.query.filter(
            (func.lower(User.username) == identity) | (func.lower(User.email) == identity)
        ).first()
        if user is None or not user.check_password(form.password.data):
            if throttle is None:
                throttle = AuthThrottle(key_hash=bucket, attempts=1, window_started=now)
                db.session.add(throttle)
            elif (now - (throttle.window_started.replace(tzinfo=timezone.utc) if throttle.window_started.tzinfo is None else throttle.window_started)).total_seconds() >= 900:
                throttle.attempts = 1
                throttle.window_started = now
            else:
                throttle.attempts += 1
            db.session.commit()
            flash("The username/email or password is incorrect.", "error")
        else:
            if throttle:
                db.session.delete(throttle)
                db.session.commit()
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
    for favorite in saved_movies:
        favorite.poster_url = safe_artwork_url(favorite.poster_url)
    return render_template("favorites.html", favorites=saved_movies)


@main.get("/watchlist")
@login_required
def watchlist():
    entries = WatchEntry.query.filter_by(user_id=current_user.id).order_by(WatchEntry.status.asc(), WatchEntry.id.desc()).all()
    movies_by_id = {movie.id: movie for movie in CatalogMovie.query.filter(CatalogMovie.id.in_([entry.movie_id for entry in entries])).all()} if entries else {}
    return render_template("watchlist.html", entries=entries, movies_by_id=movies_by_id)


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
            record = CatalogMovie.query.filter_by(source="tmdb-legacy", external_id=str(movie_id)).first()
            if record is None:
                abort(404)
            title = record.title
            poster_url = safe_artwork_url(record.poster_url) or _validated_tmdb_poster(record.poster_url or "")
            release_date = record.release_date or str(record.year or "")

        db.session.add(Favorite(
            user_id=current_user.id,
            movie_key=movie_key,
            movie_title=title,
            poster_url=poster_url,
            release_date=release_date,
            catalog_movie_id=record.id if not is_local else None,
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
        record = CatalogMovie.query.filter_by(source="tmdb-legacy", external_id=str(movie_id)).first()
        if record is None:
            abort(404)
        data = {
            "movie_key": f"tmdb:{movie_id}", "catalog_movie_id": record.id,
            "title": record.title,
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
            review = Review(user_id=current_user.id, movie_key=data["movie_key"], catalog_movie_id=data.get("catalog_movie_id"))
            db.session.add(review)
        else:
            db.session.add(ReviewHistory(review_id=review.id, rating=review.rating, review_text=review.review_text,
                                         spoiler_paragraphs_json=review.spoiler_paragraphs_json or []))
        review.movie_title = data.get("title", f"Movie {movie_id}")
        review.rating = form.rating.data
        review.review_text = (form.review_text.data or "").strip() or None
        review.spoiler_paragraphs_json = _spoiler_blocks(review.review_text)
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
    review_filter = Review.movie_key == movie_key
    favorite_filter = Favorite.movie_key == movie_key
    if data.get("is_catalog"):
        review_filter = or_(review_filter, Review.catalog_movie_id == data["id"])
        favorite_filter = or_(favorite_filter, Favorite.catalog_movie_id == data["id"])
    review_query = Review.query.filter(review_filter)
    if current_user.is_authenticated:
        review_query = review_query.filter(or_(Review.is_hidden.is_(False), Review.user_id == current_user.id))
    else:
        review_query = review_query.filter(Review.is_hidden.is_(False))
    reviews = review_query.order_by(Review.updated_at.desc()).all()
    if request.args.get("sort") == "helpful":
        reviews.sort(key=lambda item: (-item.helpful_votes, item.updated_at), reverse=False)
    average_rating = db.session.query(func.avg(Review.rating)).filter(review_filter).scalar()
    my_review = None
    is_favorite = False
    if current_user.is_authenticated:
        my_review = Review.query.filter(review_filter, Review.user_id == current_user.id).order_by(Review.updated_at.desc()).first()
        is_favorite = Favorite.query.filter(favorite_filter, Favorite.user_id == current_user.id).first() is not None
    form = ReviewForm(data={"rating": my_review.rating, "review_text": my_review.review_text or ""}) if my_review else ReviewForm()
    return render_template(
        "movie.html",
        movie=data,
        reviews=reviews,
        average_rating=average_rating,
        review_form=form,
        my_review=my_review,
        is_favorite=is_favorite,
        collections=UserCollection.query.filter_by(user_id=current_user.id).all() if current_user.is_authenticated else [],
        tmdb_error=tmdb_error,
    ), status


def _validated_tmdb_poster(poster_url):
    candidate = (poster_url or "").strip()[:500]
    parsed = urlsplit(candidate)
    if parsed.scheme == "https" and parsed.netloc == "image.tmdb.org" and parsed.path.startswith("/t/p/"):
        return candidate
    return None


def _movie_url(movie_id, is_local):
    if is_local:
        return url_for("main.movie", movie_id=movie_id, local=1)
    return url_for("main.movie", movie_id=movie_id, source="tmdb")


def _read_tmdb_cache(cache_key, default=None):
    entry = db.session.get(TMDBCacheEntry, cache_key)
    return entry.payload if entry is not None else default


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


def _local_static_url(filename):
    if not filename:
        return None
    static_root = os.path.realpath(current_app.static_folder)
    local_path = os.path.realpath(os.path.join(static_root, filename))
    try:
        if os.path.commonpath((static_root, local_path)) != static_root or not os.path.isfile(local_path):
            return None
    except (OSError, ValueError):
        return None
    relative_path = os.path.relpath(local_path, static_root).replace(os.sep, "/")
    return url_for("static", filename=relative_path)


def _local_movies():
    return [
        {
            "id": movie.id,
            "title": movie.title,
            "poster_url": _local_static_url(movie.poster),
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
        "poster_url": _local_static_url(movie.poster),
        "genres": (movie.genre or "").split(),
        "rating": None,
        "release_date": str(movie.release_year or ""),
        "release_year": movie.release_year,
        "runtime": movie.duration,
        "overview": movie.description or "No description is available.",
        "cast": [
            {"name": person.actor_name, "character": person.character_name, "photo_url": _local_static_url(person.photo)}
            for person in cast
        ],
        "directors": [person.name for person in crew if person.role and person.role.casefold() == "director"],
        "crew": [
            {"name": person.name, "job": person.role or "Crew", "department": "", "photo_url": _local_static_url(person.photo)}
            for person in crew
        ],
        "trailer_url": movie.trailer,
        "is_local": True,
    }
