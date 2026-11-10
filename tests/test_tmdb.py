import os
import re
import unittest
from unittest.mock import Mock, patch

import requests

from app.tmdb import TMDBError, movie_details, movie_genres, movies_by_genre, popular_movies, search_movies
from app import create_app
from app import db
from app.models import CatalogMovie, Favorite, Movie, Review, TMDBCacheEntry, User


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

    def test_homepage_uses_local_catalog_without_external_calls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RRR", response.data)
        self.assertIn(b"/film/", response.data)

    def test_movie_page_uses_saved_tmdb_details_without_network(self):
        cached = {
            "id": 74, "title": "Cached Detail Film", "poster_url": None, "genres": ["Action"],
            "rating": 8.2, "release_date": "2024-06-01", "release_year": "2024",
            "overview": "Saved movie description.", "directors": [], "cast": [], "crew": [],
        }
        with self.app.app_context():
            from app.routes import _write_tmdb_cache
            _write_tmdb_cache("movie:74", cached)

        response = self.client.get("/movie/74?source=tmdb")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cached Detail Film", response.data)
        self.assertIn(b"Saved movie description.", response.data)
        self.assertIn(b"preserved movie information", response.data)

    def test_homepage_hero_comes_from_catalog_and_not_fixed_movie_names(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Featured: <span>RRR</span>", response.data)
        self.assertNotIn(b"Avengers: Doomsday", response.data)

    def test_local_movie_page_renders_saved_crew_without_provider(self):
        with self.app.app_context():
            from app.models import Cast, Crew
            db.session.add(Movie(id=42, title="Example", genre="Drama", release_year=2025,
                                 duration="110", description="A summary", trailer="https://www.youtube.com/watch?v=abc"))
            db.session.add(Crew(movie_id=42, name="Director Name", role="Director"))
            db.session.add(Cast(movie_id=42, actor_name="Actor Name", character_name="Lead"))
            db.session.commit()
        response = self.client.get("/movie/42?local=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Director Name", response.data)
        self.assertIn(b"Actor Name", response.data)
        self.assertIn(b"watch?v=abc", response.data)

    @patch("app.routes.tmdb.search_movies")
    def test_search_uses_tmdb_after_local_catalog_miss_and_caches_detail(self, search_movies):
        search_movies.return_value = [{
            "id": 299536,
            "title": "Avengers: Infinity War",
            "poster_url": "https://image.tmdb.org/t/p/w500/infinity-war.jpg",
            "genres": ["Action", "Adventure"],
            "rating": 8.2,
            "release_date": "2018-04-25",
            "release_year": "2018",
            "overview": "The Avengers face a powerful new threat.",
        }]

        response = self.client.get("/search?q=avengers+infinity+war")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Avengers: Infinity War", response.data)
        self.assertIn(b"https://image.tmdb.org/t/p/w500/infinity-war.jpg", response.data)
        self.assertNotIn(b"Endless stories", response.data)
        search_movies.assert_called_once_with("avengers infinity war")
        detail_response = self.client.get("/movie/299536?source=tmdb")
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn(b"Avengers: Infinity War", detail_response.data)

    @patch("app.routes.tmdb.search_movies")
    def test_search_empty_and_invalid_queries_have_clear_states(self, search_movies):
        search_movies.return_value = []
        response = self.client.get("/search?q=not+a+real+movie")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No movies found for", response.data)
        self.assertIn(b"Check the spelling", response.data)
        self.assertNotIn(b"Endless stories", response.data)

        response = self.client.get("/search?q=%25%25%25")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Try a title with letters or numbers", response.data)
        search_movies.assert_called_once_with("not a real movie")

    @patch("app.routes.tmdb.search_movies")
    def test_search_provider_failure_has_a_clean_fallback_state(self, search_movies):
        from app.tmdb import TMDBError
        search_movies.side_effect = TMDBError("network details are intentionally hidden")
        response = self.client.get("/search?q=unavailable+film")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"We couldn", response.data)
        self.assertIn(b"Online movie search is temporarily unavailable", response.data)
        self.assertNotIn(b"Endless stories", response.data)
        self.assertNotIn(b"network details", response.data)

    @patch("app.routes.tmdb.search_movies")
    def test_search_keeps_local_catalog_results_without_provider_request(self, search_movies):
        response = self.client.get("/search?q=matrix")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"The Matrix", response.data)
        search_movies.assert_not_called()

    def test_genres_page_uses_local_catalog(self):
        response = self.client.get("/genres?genre=Action")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Browse by genre", response.data)
        self.assertIn(b"Drama", response.data)
        self.assertIn(b"The Matrix", response.data)
        self.assertIn(b"/film/", response.data)

    def test_genres_page_groups_local_catalog_horizontally(self):
        response = self.client.get("/genres")
        page = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('aria-label="Action movies"', page)
        self.assertIn('aria-label="Comedy movies"', page)
        self.assertIn('aria-label="Drama movies"', page)
        self.assertLess(page.index('id="genre-row-1"'), page.index('id="genre-row-2"'))


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
            db.session.add(CatalogMovie(id=42, source="tmdb-legacy", external_id="42", title="Remote TMDB Film", year=2024,
                                         poster_url="https://image.tmdb.org/t/p/w500/canonical.jpg", release_date="2024-01-01"))
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

    def test_favorites_require_login_and_can_be_saved_and_removed(self):
        protected = self.client.get("/favorites")
        self.assertEqual(protected.status_code, 302)
        self.register()

        saved = self.client.post("/movie/42/favorite?source=tmdb", data={
            "movie_title": "Remote TMDB Film",
            "poster_url": "https://image.tmdb.org/t/p/w500/poster.jpg",
            "release_date": "2024-01-02",
        })
        self.assertEqual(saved.status_code, 302)
        page = self.client.get("/favorites")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Remote TMDB Film", page.data)
        with self.app.app_context():
            favorite = Favorite.query.one()
            self.assertEqual(favorite.movie_key, "tmdb:42")
            self.assertEqual(favorite.poster_url, "https://image.tmdb.org/t/p/w500/canonical.jpg")

        removed = self.client.post("/movie/42/favorite?source=tmdb")
        self.assertEqual(removed.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Favorite.query.count(), 0)

    def test_favorites_support_local_movies_and_reject_untrusted_posters(self):
        self.register()
        self.client.post("/movie/42/favorite?source=tmdb", data={
            "movie_title": "Remote Film",
            "poster_url": "https://example.com/untrusted.jpg",
        })
        self.client.post("/movie/1/favorite?local=1")
        with self.app.app_context():
            remote = Favorite.query.filter_by(movie_key="tmdb:42").one()
            local = Favorite.query.filter_by(movie_key="local:1").one()
            self.assertEqual(remote.movie_title, "Remote TMDB Film")
            self.assertEqual(remote.poster_url, "https://image.tmdb.org/t/p/w500/canonical.jpg")
            self.assertEqual(local.movie_title, "Local Film")
            self.assertTrue(local.is_local_movie)

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
