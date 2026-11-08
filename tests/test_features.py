import csv
import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from unittest.mock import Mock

from werkzeug.datastructures import FileStorage

from app import create_app, db
from app.catalog import as_card
from app.models import (CatalogMovie, CineRoomVote, Favorite, ImportPreview, Review,
                        ReviewHistory, ReviewReport, ReviewVote, User, WatchEntry)


class CineverseFeatureTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": "feature-test-secret",
                               "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "WTF_CSRF_ENABLED": False})
        self.client = self.app.test_client()
        with self.app.app_context():
            self.movie_id = CatalogMovie.query.filter_by(title="RRR").one().id
            self.second_id = CatalogMovie.query.filter_by(title="The Matrix").one().id
            db.session.add_all([
                CatalogMovie(source="fixture", external_id="dune-1984", title="Dune", year=1984, language="en"),
                CatalogMovie(source="fixture", external_id="dune-2021", title="Dune", year=2021, language="en"),
            ])
            db.session.commit()

    def register(self, client=None, username="watcher"):
        client = client or self.client
        return client.post("/register", data={"username": username, "email": f"{username}@example.test",
                          "password": "safe-password-123", "confirm_password": "safe-password-123"}, follow_redirects=True)

    def test_movie_detail_and_cinematch_render_from_local_catalog_without_network(self):
        response = self.client.get(f"/film/{self.movie_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RRR", response.data)
        response = self.client.post("/cine-match", data={"mood": "adventurous", "language": "te"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RRR", response.data)
        self.assertIn(b"Matches your chosen", response.data)

    def test_legacy_rating_shape_is_normalized_before_card_rendering(self):
        with self.app.app_context():
            movie = db.session.get(CatalogMovie, self.movie_id)
            movie.ratings_json = {"TMDB": {"value": 8.4, "source": "TMDB"}}
            db.session.commit()
            card = as_card(movie)
            self.assertEqual(card["rating"], 8.4)
            self.assertEqual(card["rating_source"], "TMDB")
        response = self.client.get("/genres")
        self.assertEqual(response.status_code, 200)

    def test_watch_status_and_private_diary_note(self):
        self.register()
        response = self.client.post(f"/film/{self.movie_id}/watch", data={"status": "watched", "private_note": "my private note"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            entry = WatchEntry.query.one()
            self.assertEqual(entry.status, "watched")
            self.assertEqual(entry.private_note, "my private note")
        self.assertIn(b"my private note", self.client.get("/watchlist").data)
        self.assertNotIn(b"my private note", self.client.get(f"/film/{self.movie_id}").data)

    def test_spoiler_paragraphs_history_helpful_vote_and_report(self):
        self.register(username="writer")
        self.client.post(f"/film/{self.movie_id}/review", data={"rating": 8, "review_text": "Loved the pacing.\n[[spoiler]]The ending reveals it all.[[/spoiler]]"})
        self.client.post(f"/film/{self.movie_id}/review", data={"rating": 9, "review_text": "Opening.\n[[spoiler]]Ending is revealed.[[/spoiler]]"})
        with self.app.app_context():
            review = Review.query.one()
            review_id = review.id
            self.assertEqual(ReviewHistory.query.count(), 1)
            self.assertEqual(review.rating, 9)
        page = self.client.get(f"/film/{self.movie_id}")
        self.assertIn(b"Spoiler", page.data)
        other = self.app.test_client()
        self.register(other, username="reader")
        other.post(f"/reviews/{review_id}/helpful")
        other.post(f"/reviews/{review_id}/report", data={"reason": "Abusive language"})
        with self.app.app_context():
            self.assertEqual(ReviewVote.query.count(), 1)
            self.assertEqual(ReviewReport.query.one().reason, "Abusive language")
            report_id = ReviewReport.query.one().id
            writer_id = User.query.filter_by(username="writer").one().id
        with patch.dict(os.environ, {"CINEVERSE_MODERATOR_IDS": str(writer_id)}):
            self.assertEqual(self.client.get("/moderation").status_code, 200)
            self.client.post("/moderation", data={"report_id": report_id, "action": "hide"})
        with self.app.app_context():
            self.assertTrue(db.session.get(Review, review_id).is_hidden)

    def test_csv_import_preview_exact_match_duplicate_and_undo(self):
        self.register()
        source = io.BytesIO(b"title,year,watched\nRRR,2022,yes\nDune,,no\nMissing Film,2020,no\n")
        response = self.client.post("/data-import", data={"file": (source, "watchlist.csv")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ambiguous", response.data)
        self.assertIn(b"not found", response.data)
        with self.app.app_context():
            preview_id = ImportPreview.query.one().id
        response = self.client.post("/data-import", data={"preview_id": preview_id, "confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(WatchEntry.query.count(), 1)
        self.client.post("/data-import", data={"preview_id": preview_id, "undo_id": preview_id})
        with self.app.app_context():
            self.assertEqual(WatchEntry.query.count(), 0)

    def test_cineduel_room_and_dna_use_user_activity(self):
        self.register()
        response = self.client.get("/cinema-dna")
        self.assertIn(b"profile will grow", response.data)
        response = self.client.post("/cine-duel", data={"winner": self.movie_id, "loser": self.second_id})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            owner_id = User.query.filter_by(username="watcher").one().id
            room = None
        self.client.post("/cine-room", data={})
        with self.app.app_context():
            from app.models import CineRoom, DuelPreference
            room = CineRoom.query.one()
            self.assertEqual(DuelPreference.query.filter_by(user_id=owner_id).count(), 1)
            code = room.code
        participant = self.app.test_client()
        participant.get(f"/cine-room/{code}")
        participant.post(f"/cine-room/{code}", data={"movie_id": self.second_id})
        self.assertIn(b"private until", self.client.get(f"/cine-room/{code}").data)
        self.client.post(f"/cine-room/{code}", data={"movie_id": self.movie_id})
        self.client.post(f"/cine-room/{code}", data={"action": "reveal"})
        with self.app.app_context():
            self.assertEqual(CineRoomVote.query.filter_by(room_id=room.id).count(), 2)
        self.client.get(f"/film/{self.movie_id}")
        self.client.post(f"/film/{self.movie_id}/watch", data={"status": "watched"})
        self.assertIn(b"watched", self.client.get("/cinema-dna").data)

    def test_signin_throttles_repeated_bad_password_attempts(self):
        self.register()
        self.client.post("/logout")
        last = None
        for _ in range(9):
            last = self.client.post("/login", data={"identifier": "watcher", "password": "wrong-password"})
        self.assertEqual(last.status_code, 429)


class LegacyMigrationTests(unittest.TestCase):
    def test_additive_migration_preserves_legacy_rows_and_links_exact_ids(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder) / "legacy.sqlite3"
            legacy_conn = sqlite3.connect(path)
            try:
                conn = legacy_conn
                conn.executescript("""
                    CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(32) UNIQUE NOT NULL,
                      email VARCHAR(254) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL,
                      created_at DATETIME NOT NULL);
                    CREATE TABLE movie (id INTEGER PRIMARY KEY, title VARCHAR(100) NOT NULL, genre VARCHAR(100),
                      language VARCHAR(50), release_year INTEGER, duration VARCHAR(20), imdb_rating FLOAT,
                      description TEXT, poster VARCHAR(200), trailer VARCHAR(300));
                    CREATE TABLE review (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, movie_key VARCHAR(64) NOT NULL,
                      movie_title VARCHAR(200) NOT NULL, rating INTEGER NOT NULL, review_text VARCHAR(2000),
                      created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, UNIQUE(user_id,movie_key));
                    CREATE TABLE favorite (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, movie_key VARCHAR(64) NOT NULL,
                      movie_title VARCHAR(200) NOT NULL, poster_url VARCHAR(500), release_date VARCHAR(32),
                      created_at DATETIME NOT NULL, UNIQUE(user_id,movie_key));
                    CREATE TABLE cast (id INTEGER PRIMARY KEY, movie_id INTEGER, actor_name VARCHAR(100) NOT NULL, character_name VARCHAR(100), photo VARCHAR(200));
                    CREATE TABLE crew (id INTEGER PRIMARY KEY, movie_id INTEGER, name VARCHAR(100) NOT NULL, role VARCHAR(50), photo VARCHAR(200));
                    CREATE TABLE tmdb_cache_entry (cache_key VARCHAR(200) PRIMARY KEY, payload JSON NOT NULL, updated_at DATETIME NOT NULL);
                    INSERT INTO user VALUES(1,'preserved','preserved@example.test','existing-hash','2025-01-01');
                    INSERT INTO movie VALUES(7,'Legacy Local','Drama','Telugu',2020,'120 min',NULL,'Saved plot',NULL,NULL);
                    INSERT INTO review VALUES(3,1,'local:7','Legacy Local',8,'User review','2025-01-01','2025-01-01');
                    INSERT INTO favorite VALUES(4,1,'local:7','Legacy Local',NULL,'2020', '2025-01-01');
                """)
            finally:
                legacy_conn.close()
            app = create_app({"TESTING": True, "SECRET_KEY": "migration-test", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path.as_posix()}", "WTF_CSRF_ENABLED": False})
            with app.app_context():
                self.assertEqual(User.query.one().password_hash, "existing-hash")
                review = Review.query.filter_by(id=3).one()
                favorite = Favorite.query.filter_by(id=4).one()
                self.assertEqual(review.catalog_movie_id, 1)
                self.assertEqual(favorite.catalog_movie_id, 1)
                self.assertEqual(CatalogMovie.query.filter_by(source="legacy", external_id="7").one().title, "Legacy Local")
                from sqlalchemy import inspect
                self.assertTrue(inspect(db.engine).has_table("schema_migrations"))
                db.session.remove()
                db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
