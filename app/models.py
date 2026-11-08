from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), nullable=False, unique=True, index=True)
    email = db.Column(db.String(254), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    reviews = db.relationship("Review", back_populates="user", lazy="select")
    favorites = db.relationship("Favorite", back_populates="user", lazy="select", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Review(db.Model):
    __table_args__ = (
        db.UniqueConstraint("user_id", "movie_key", name="uq_review_user_movie"),
        db.CheckConstraint("rating >= 1 AND rating <= 10", name="ck_review_rating_range"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    # Keys are stable without requiring a TMDB row to be inserted into the legacy movie table.
    movie_key = db.Column(db.String(64), nullable=False, index=True)
    catalog_movie_id = db.Column(db.Integer, db.ForeignKey("catalog_movie.id", ondelete="SET NULL"), index=True)
    movie_title = db.Column(db.String(200), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    review_text = db.Column(db.String(2000))
    spoiler_paragraphs_json = db.Column(db.JSON, nullable=False, default=list)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", back_populates="reviews")

    @property
    def is_local_movie(self):
        return self.movie_key.startswith("local:")

    @property
    def is_catalog_movie(self):
        return self.movie_key.startswith("catalog:")

    @property
    def movie_id(self):
        return int(self.movie_key.partition(":")[2])

    @property
    def helpful_votes(self):
        return ReviewVote.query.filter_by(review_id=self.id).count()


class Favorite(db.Model):
    __table_args__ = (
        db.UniqueConstraint("user_id", "movie_key", name="uq_favorite_user_movie"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    movie_key = db.Column(db.String(64), nullable=False, index=True)
    catalog_movie_id = db.Column(db.Integer, db.ForeignKey("catalog_movie.id", ondelete="SET NULL"), index=True)
    movie_title = db.Column(db.String(200), nullable=False)
    poster_url = db.Column(db.String(500))
    release_date = db.Column(db.String(32))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", back_populates="favorites")

    @property
    def is_local_movie(self):
        return self.movie_key.startswith("local:")

    @property
    def movie_id(self):
        return int(self.movie_key.partition(":")[2])

    @property
    def is_catalog_movie(self):
        return self.movie_key.startswith("catalog:")


class TMDBCacheEntry(db.Model):
    """A small, credential-free cache of public TMDB responses for offline viewing."""

    cache_key = db.Column(db.String(200), primary_key=True)
    payload = db.Column(db.JSON, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class Movie(db.Model):

    id=db.Column(db.Integer, primary_key=True)

    title=db.Column(db.String(100), nullable=False)
    genre=db.Column(db.String(100))
    language=db.Column(db.String(50))
    release_year=db.Column(db.Integer)
    duration=db.Column(db.String(20))

    imdb_rating=db.Column(db.Float)

    description=db.Column(db.Text)


    poster=db.Column(db.String(200))
    trailer=db.Column(db.String(300))

class Cast(db.Model):
    id=db.Column(db.Integer, primary_key=True)

    movie_id=db.Column(db.Integer,db.ForeignKey('movie.id'))

    actor_name=db.Column(db.String(100), nullable=False)
    character_name=db.Column(db.String(100))
    photo=db.Column(db.String(200))

class Crew(db.Model):
    id=db.Column(db.Integer, primary_key=True)

    movie_id=db.Column(db.Integer,db.ForeignKey('movie.id'))

    name=db.Column(db.String(100), nullable=False)
    role=db.Column(db.String(50))
    photo=db.Column(db.String(200))


class CatalogMovie(db.Model):
    """Provider-neutral movie record. Provider data is optional; the local catalog is usable offline."""
    __tablename__ = "catalog_movie"
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(32), nullable=False, default="wikidata", index=True)
    external_id = db.Column(db.String(80), nullable=False, index=True)
    imdb_id = db.Column(db.String(16), index=True)
    title = db.Column(db.String(240), nullable=False, index=True)
    original_title = db.Column(db.String(240))
    year = db.Column(db.Integer, index=True)
    release_date = db.Column(db.String(32))
    overview = db.Column(db.Text)
    genres_json = db.Column(db.JSON, nullable=False, default=list)
    language = db.Column(db.String(32), index=True)
    country = db.Column(db.String(64))
    runtime_minutes = db.Column(db.Integer)
    poster_url = db.Column(db.String(500))
    artwork_url = db.Column(db.String(500))
    ratings_json = db.Column(db.JSON, nullable=False, default=dict)
    cast_json = db.Column(db.JSON, nullable=False, default=list)
    crew_json = db.Column(db.JSON, nullable=False, default=list)
    aliases_json = db.Column(db.JSON, nullable=False, default=list)
    mood_tags_json = db.Column(db.JSON, nullable=False, default=list)
    provenance_json = db.Column(db.JSON, nullable=False, default=dict)
    fetched_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint("source", "external_id", name="uq_catalog_source_external"),)


class WatchEntry(db.Model):
    __tablename__ = "watch_entry"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    movie_id = db.Column(db.Integer, db.ForeignKey("catalog_movie.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default="watchlist")
    watched_on = db.Column(db.Date)
    private_note = db.Column(db.Text)
    __table_args__ = (db.UniqueConstraint("user_id", "movie_id", name="uq_watch_user_movie"),)


class ReviewHistory(db.Model):
    __tablename__ = "review_history"
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey("review.id", ondelete="CASCADE"), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    review_text = db.Column(db.String(2000))
    spoiler_paragraphs_json = db.Column(db.JSON, nullable=False, default=list)
    saved_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ReviewVote(db.Model):
    __tablename__ = "review_vote"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    review_id = db.Column(db.Integer, db.ForeignKey("review.id", ondelete="CASCADE"), nullable=False, index=True)
    __table_args__ = (db.UniqueConstraint("user_id", "review_id", name="uq_vote_user_review"),)


class ReviewReport(db.Model):
    __tablename__ = "review_report"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    review_id = db.Column(db.Integer, db.ForeignKey("review.id", ondelete="CASCADE"), nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="open")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    review = db.relationship("Review")
    __table_args__ = (db.UniqueConstraint("user_id", "review_id", name="uq_report_user_review"),)


class DuelPreference(db.Model):
    __tablename__ = "duel_preference"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    winner_movie_id = db.Column(db.Integer, db.ForeignKey("catalog_movie.id", ondelete="CASCADE"), nullable=False)
    loser_movie_id = db.Column(db.Integer, db.ForeignKey("catalog_movie.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CineRoom(db.Model):
    __tablename__ = "cine_room"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), nullable=False, unique=True, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    is_revealed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class CineRoomVote(db.Model):
    __tablename__ = "cine_room_vote"
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("cine_room.id", ondelete="CASCADE"), nullable=False, index=True)
    voter_token = db.Column(db.String(64), nullable=False)
    movie_id = db.Column(db.Integer, db.ForeignKey("catalog_movie.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint("room_id", "voter_token", name="uq_room_voter"),)


class UserCollection(db.Model):
    __tablename__ = "user_collection"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(300))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    items = db.relationship("UserCollectionItem", cascade="all, delete-orphan", lazy="select")
    __table_args__ = (db.UniqueConstraint("user_id", "title", name="uq_collection_user_title"),)


class UserCollectionItem(db.Model):
    __tablename__ = "user_collection_item"
    id = db.Column(db.Integer, primary_key=True)
    collection_id = db.Column(db.Integer, db.ForeignKey("user_collection.id", ondelete="CASCADE"), nullable=False, index=True)
    movie_id = db.Column(db.Integer, db.ForeignKey("catalog_movie.id", ondelete="CASCADE"), nullable=False)
    movie = db.relationship("CatalogMovie")
    __table_args__ = (db.UniqueConstraint("collection_id", "movie_id", name="uq_collection_movie"),)


class ImportPreview(db.Model):
    __tablename__ = "import_preview"
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    rows_json = db.Column(db.JSON, nullable=False)
    imported_watch_ids_json = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class AuthThrottle(db.Model):
    __tablename__ = "auth_throttle"
    key_hash = db.Column(db.String(64), primary_key=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    window_started = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
