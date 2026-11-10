import unittest
from unittest.mock import Mock

import requests

from app.catalog import OMDbProvider, ProviderError, WikidataProvider, safe_artwork_url


class ProviderAdapterTests(unittest.TestCase):
    def test_omdb_normalizes_missing_values_and_labels_rating_sources(self):
        result = OMDbProvider.normalize({
            "Title": "Example", "Year": "2020", "Runtime": "101 min", "Genre": "Drama, Mystery",
            "Plot": "N/A", "Actors": "A, B", "Director": "Director",
            "Poster": "N/A", "Ratings": [{"Source": "Internet Movie Database", "Value": "7.3/10"}],
        })
        self.assertEqual(result["year"], 2020)
        self.assertEqual(result["runtime_minutes"], 101)
        self.assertEqual(result["overview"], None)
        self.assertIsNone(result["poster_url"])
        self.assertEqual(result["ratings"]["Internet Movie Database"]["value"], "7.3/10")
        self.assertEqual(result["ratings"]["Internet Movie Database"]["source"], "Internet Movie Database")

    def test_omdb_requires_key_and_validates_external_identity(self):
        with self.assertRaisesRegex(ProviderError, "not configured"):
            OMDbProvider(api_key="").search("Film")
        provider = OMDbProvider(api_key="test", session=Mock())
        with self.assertRaises(ValueError):
            provider.details("https://example.invalid/tt1234567")
        with self.assertRaises(ValueError):
            provider.search("Film", page=101)

    def test_omdb_uses_bounded_timeout_and_retries_then_returns_safe_error(self):
        session = Mock()
        session.get.side_effect = requests.ConnectTimeout()
        provider = OMDbProvider(api_key="test", session=session, sleeper=lambda _: None)
        with self.assertRaisesRegex(ProviderError, "could not be reached") as error:
            provider.search("Film")
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args.kwargs["timeout"], (2, 5))
        self.assertNotIn(provider.api_key, str(error.exception))  # Errors don't echo credentials.

    def test_omdb_quota_guard_stops_at_advertised_daily_limit(self):
        provider = OMDbProvider(api_key="test", session=Mock())
        provider._requests_today = provider.daily_limit
        with self.assertRaisesRegex(ProviderError, "daily request limit"):
            provider.search("Film")
        provider.session.get.assert_not_called()

    def test_wikidata_reads_known_item_with_safe_request_headers(self):
        session = Mock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"entities": {"Q42": {
            "labels": {"en": {"value": "Example film"}}, "aliases": {"en": [{"value": "Alt title"}]},
            "descriptions": {"en": {"value": "A film"}},
            "claims": {
                "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+2020-01-01T00:00:00Z"}}}}],
                "P364": [{"mainsnak": {"datavalue": {"value": {"id": "Q1860"}}}}],
            },
        }}}
        session.get.return_value = response
        data = WikidataProvider(session=session).entity("Q42")
        self.assertEqual(data["title"], "Example film")
        self.assertEqual(data["aliases"], ["Alt title"])
        self.assertEqual(data["release_dates"], ["2020-01-01"])
        self.assertEqual(data["language_ids"], ["Q1860"])
        self.assertIn("User-Agent", session.get.call_args.kwargs["headers"])

    def test_wikidata_rejects_bad_ids_and_handles_throttling(self):
        provider = WikidataProvider(session=Mock())
        with self.assertRaises(ValueError):
            provider.entity("Q1/../../secret")
        provider.session.get.return_value.status_code = 429
        with self.assertRaisesRegex(ProviderError, "slow down"):
            provider.entity("Q42")

    def test_artwork_urls_reject_non_https_and_unknown_hosts(self):
        self.assertIsNone(safe_artwork_url("javascript:alert(1)"))
        self.assertIsNone(safe_artwork_url("https://untrusted.example/poster.jpg"))
        self.assertEqual(safe_artwork_url("https://image.tmdb.org/t/p/w500/poster.jpg"), "https://image.tmdb.org/t/p/w500/poster.jpg")
        self.assertIsNone(safe_artwork_url("/static/images/avengers_endgame.png"))
        self.assertIsNone(safe_artwork_url("/static/images/tmdb-cache/movie-999999999.jpg"))


if __name__ == "__main__":
    unittest.main()

