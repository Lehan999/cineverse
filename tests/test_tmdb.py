import os
import re
import unittest
from unittest.mock import Mock, patch

import requests

from app.tmdb import TMDBError, movie_details, movie_genres, movies_by_genre, popular_movies, search_movies
from app import create_app
from app import db
from app.models import Movie, Review, User


class TMDBClientTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_credentials_returns_clear_error(self):
        with self.assertRaisesRegex(TMDBError, "credentials are not configured"):
            popular_movies()

    @patch.dict(os.environ, {"TMDB_API_KEY": "test-key"}, clear=True)
    @patch("app.tmdb.requests.get", side_effect=requests.ConnectTimeout())
    def test_connect_timeout_reports_network_problem_without_credentials(self, get):
        with self.assertRaisesRegex(TMDBError, "could not connect to TMDB over HTTPS"):
            popular_movies()

    @patch.dict(os.environ, {"TMDB_READ_ACCESS_TOKEN": "test-token"}, clear=True)
    @patch("app.tmdb.requests.get")
    def test_popular_movies_maps_tmdb_fields(self, get):
        response = Mock()
        response.json.return_value = {"results": [{
            "id": 42, "title": "Example", "poster_path": "/poster.jpg",
            "genre_ids": [28], "vote_average": 8.4, "release_date": "2024-01-02",
            "overview": "Description",
        }]}
        get.return_value = response

        movies = popular_movies()

        self.assertEqual(movies[0]["id"], 42)
        self.assertEqual(movies[0]["genres"], ["Action"])
        self.assertEqual(movies[0]["rating"], 8.4)
        self.assertTrue(movies[0]["poster_url"].endswith("/poster.jpg"))
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")

    @patch.dict(os.environ, {"TMDB_API_KEY": "test-key"}, clear=True)
    @patch("app.tmdb.requests.get")
    def test_genre_catalog_and_movies_use_tmdb_endpoints(self, get):
        genres_response = Mock()
        genres_response.json.return_value = {"genres": [{"id": 28, "name": "Action"}]}
        movies_response = Mock()
        movies_response.json.return_value = {"results": [{
            "id": 42, "title": "Example", "genre_ids": [28], "vote_average": 8.2,
            "release_date": "2025-01-01", "overview": "A story", "poster_path": None,
        }]}
        get.side_effect = [genres_response, movies_response]

        self.assertEqual(movie_genres(), [{"id": 28, "name": "Action"}])
        movies = movies_by_genre(28)

        self.assertEqual(movies[0]["title"], "Example")
        self.assertEqual(movies[0]["rating"], 8.2)
        self.assertEqual(get.call_args.kwargs["params"]["with_genres"], 28)

    @patch.dict(os.environ, {"TMDB_API_KEY": "test-key"}, clear=True)
    @patch("app.tmdb.requests.get")
    def test_search_and_details_include_credits(self, get):
        search_response = Mock()
        search_response.json.return_value = {"results": [{"id": 7, "title": "Found", "genre_ids": []}]}
        details_response = Mock()
        details_response.json.return_value = {
            "id": 7, "title": "Found", "genres": [{"name": "Drama"}], "release_date": "2025-02-03",
            "credits": {
                "cast": [{"name": "Actor", "character": "Lead", "profile_path": "/actor.jpg"}],
                "crew": [
                    {"name": "Director", "job": "Director"},
                    {"name": "Producer", "job": "Producer", "department": "Production"},
                ],
            },
            "videos": {"results": [{"site": "YouTube", "type": "Trailer", "key": "abc"}]},
        }
        get.side_effect = [search_response, details_response]

        found = search_movies("Found")
        details = movie_details(7)

        self.assertEqual(found[0]["title"], "Found")
        self.assertEqual(details["directors"], ["Director"])
        self.assertEqual(details["cast"][0]["character"], "Lead")
        self.assertEqual(details["release_date"], "2025-02-03")
        self.assertEqual(details["crew"][0]["name"], "Producer")
        self.assertEqual(details["trailer_url"], "https://www.youtube.com/watch?v=abc")
        self.assertIn("credits,videos", get.call_args.kwargs["params"]["append_to_response"])


class FlaskPageTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()

    @patch("app.routes.popular_movies")
    def test_homepage_renders_tmdb_movie(self, popular):
        popular.return_value = [{
            "id": 42, "title": "Example", "poster_url": "https://image.tmdb.org/t/p/w500/a.jpg",
            "genres": ["Action"], "rating": 8.1, "overview": "A summary",
        }]

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Example", response.data)
        self.assertIn(b"/movie/42", response.data)
        self.assertIn(b'class="movie-container"', response.data)
        self.assertNotIn(b'class="genre-rows"', response.data)

    @patch("app.routes.movie_details")
    def test_movie_page_renders_director_cast_and_trailer(self, details):
        details.return_value = {
            "id": 42, "title": "Example", "poster_url": None, "genres": ["Drama"],
            "rating": 8.1, "release_year": "2025", "runtime": 110, "overview": "A summary",
            "directors": ["Director Name"], "cast": [{"name": "Actor Name", "character": "Lead", "photo_url": None}],
            "trailer_url": "https://www.youtube.com/watch?v=abc",
        }

        response = self.client.get("/movie/42")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Director Name", response.data)
        self.assertIn(b"Actor Name", response.data)
        self.assertIn(b"watch?v=abc", response.data)

    @patch("app.routes.search_movies")
    def test_search_form_route_renders_results(self, search):
        search.return_value = [{"id": 42, "title": "Found", "poster_url": None, "genres": [], "rating": None}]

        response = self.client.get("/search?q=Found")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Found", response.data)

    @patch("app.routes.movies_by_genre")
    @patch("app.routes.movie_genres")
    def test_genres_page_shows_categories_and_selected_movies(self, get_genres, get_movies):
        get_genres.return_value = [{"id": 28, "name": "Action"}, {"id": 18, "name": "Drama"}]
        get_movies.return_value = [{
            "id": 42, "title": "Action Film", "poster_url": None, "genres": ["Action"],
            "rating": 8.2, "release_date": "2025-01-01", "overview": "A story",
        }]

        response = self.client.get("/genres?genre=28")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Browse by genre", response.data)
        self.assertIn(b"Drama", response.data)
        self.assertIn(b"Action Film", response.data)
        self.assertIn(b"/movie/42?source=tmdb", response.data)
        get_movies.assert_called_once_with(28)

    @patch("app.routes.movies_by_genre")
    @patch("app.routes.movie_genres")
    def test_genres_page_shows_multiple_horizontal_genre_rows(self, get_genres, get_movies):
        genre_items = [
            {"id": 28, "name": "Action"},
            {"id": 35, "name": "Comedy"},
            {"id": 18, "name": "Drama"},
            {"id": 12, "name": "Adventure"},
        ]
        get_genres.return_value = genre_items
        get_movies.side_effect = lambda genre_id: [{
            "id": genre_id, "title": f"{next(g['name'] for g in genre_items if g['id'] == genre_id)} film",
            "poster_url": None, "genres": [], "rating": 7.5, "release_date": "2025-01-01",
            "overview": "A story",
        }]

        response = self.client.get("/genres")
        page = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('aria-label="Action movies"', page)
        self.assertIn('aria-label="Comedy movies"', page)
        self.assertIn('aria-label="Drama movies"', page)
        self.assertIn('aria-label="Adventure movies"', page)
        self.assertIn("Action film", page)
        self.assertIn("Comedy film", page)
        self.assertLess(page.index('id="genre-row-1"'), page.index('id="genre-row-2"'))
        self.assertEqual(get_movies.call_count, 4)


class AccountAndReviewTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.session.add(Movie(id=1, title="Local Film", genre="Drama", release_year=2024, description="A saved film."))
            db.session.commit()

    def register(self):
        return self.client.post("/register", data={
            "username": "movie_fan",
            "email": "fan@example.com",
            "password": "safe-password-123",
            "confirm_password": "safe-password-123",
        }, follow_redirects=True)

    def test_registration_hashes_password_and_logs_in(self):
        response = self.register()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My reviews", response.data)
        with self.app.app_context():
            user = User.query.filter_by(username="movie_fan").one()
            self.assertTrue(user.check_password("safe-password-123"))
            self.assertNotEqual(user.password_hash, "safe-password-123")

    def test_registration_validation_rejects_short_password(self):
        response = self.client.post("/register", data={
            "username": "ab", "email": "bad-email", "password": "short", "confirm_password": "short",
        })

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(User.query.count(), 0)

    def test_login_logout_and_protected_account(self):
        protected = self.client.get("/account")
        self.assertEqual(protected.status_code, 302)
        self.register()
        self.client.post("/logout")
        signed_out = self.client.get("/account")
        self.assertEqual(signed_out.status_code, 302)
        self.assertIn("/login", signed_out.headers["Location"])
        signed_in = self.client.post("/login", data={
            "identifier": "fan@example.com",
            "password": "safe-password-123",
        }, follow_redirects=True)
        self.assertEqual(signed_in.status_code, 200)
        self.assertIn(b"Signed in as", signed_in.data)

    def test_review_requires_login_and_can_be_updated(self):
        anonymous = self.client.post("/movie/1/review?local=1", data={"rating": 8, "review_text": "A good film."})
        self.assertEqual(anonymous.status_code, 302)
        self.register()

        first = self.client.post("/movie/1/review?local=1", data={"rating": 8, "review_text": "A good film."})
        second = self.client.post("/movie/1/review?local=1", data={"rating": 9, "review_text": "A great film."})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        page = self.client.get("/movie/1?local=1")

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"A great film.", page.data)
        self.assertIn(b"9/10", page.data)
        self.assertIn(b"Community average", page.data)
        with self.app.app_context():
            self.assertEqual(Review.query.count(), 1)
            self.assertEqual(Review.query.one().rating, 9)

    def test_tmdb_reviews_use_tmdb_ids_and_reject_out_of_range_rating(self):
        self.register()

        invalid = self.client.post("/movie/42/review?source=tmdb", data={
            "rating": 11,
            "review_text": "Invalid rating should not be stored.",
            "movie_title": "Remote TMDB Film",
        })
        self.assertEqual(invalid.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Review.query.count(), 0)

        valid = self.client.post("/movie/42/review?source=tmdb", data={
            "rating": 7,
            "review_text": "A remote movie review.",
            "movie_title": "Remote TMDB Film",
        })
        self.assertEqual(valid.status_code, 302)
        with self.app.app_context():
            review = Review.query.one()
            self.assertEqual(review.movie_key, "tmdb:42")
            self.assertEqual(review.movie_title, "Remote TMDB Film")

class CSRFSecurityTests(unittest.TestCase):
    def test_registration_form_token_is_accepted(self):
        app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": True,
        })
        client = app.test_client()
        page = client.get("/register")
        match = re.search(rb'name="csrf_token" type="hidden" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)

        response = client.post("/register", data={
            "csrf_token": match.group(1).decode(),
            "username": "movie_fan",
            "email": "fan@example.com",
            "password": "safe-password-123",
            "confirm_password": "safe-password-123",
        })

        self.assertEqual(response.status_code, 302)

    def test_post_without_csrf_token_is_rejected(self):
        app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": True,
        })
        response = app.test_client().post("/register", data={
            "username": "movie_fan",
            "email": "fan@example.com",
            "password": "safe-password-123",
            "confirm_password": "safe-password-123",
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"form expired or could not be verified", response.data)


if __name__ == "__main__":
    unittest.main()
