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
    movie_title = db.Column(db.String(200), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    review_text = db.Column(db.String(2000))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    user = db.relationship("User", back_populates="reviews")

    @property
    def is_local_movie(self):
        return self.movie_key.startswith("local:")

    @property
    def movie_id(self):
        return int(self.movie_key.partition(":")[2])


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
