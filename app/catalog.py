"""Local-first discovery and optional metadata provider adapters."""
from datetime import date, datetime, timezone
import os
import re
import time

import requests

from app import db
from app.models import CatalogMovie


# Facts here are limited to title, release year, language and Wikidata identity.
# Genre/mood selections are explicitly marked as Cineverse editorial tags.
STARTER_MOVIES = [
    ("wikidata", "Q60424165", "RRR", "2022", "te", "India", ["Action", "Drama"], ["adventurous", "intense"]),
    ("wikidata", "Q13897247", "Baahubali: The Beginning", "2015", "te", "India", ["Action", "Fantasy"], ["adventurous"]),
    ("wikidata", "Q39048745", "Mahanati", "2018", "te", "India", ["Drama"], ["emotional"]),
    ("wikidata", "Q97119189", "Radhe Shyam", "2022", "te", "India", ["Romance", "Drama"], ["emotional"]),
    ("wikidata", "Q229633", "3 Idiots", "2009", "hi", "India", ["Comedy", "Drama"], ["lighthearted"]),
    ("wikidata", "Q843949", "Lagaan", "2001", "hi", "India", ["Drama", "Sport"], ["adventurous"]),
    ("wikidata", "Q15907466", "Queen", "2014", "hi", "India", ["Comedy", "Drama"], ["lighthearted"]),
    ("wikidata", "Q23647131", "Pink", "2016", "hi", "India", ["Drama", "Thriller"], ["intense"]),
    ("wikidata", "Q83495", "The Matrix", "1999", "en", "United States", ["Action", "Science Fiction"], ["intense"]),
    ("wikidata", "Q47703", "The Godfather", "1972", "en", "United States", ["Crime", "Drama"], ["intense"]),
    ("wikidata", "Q163872", "The Dark Knight", "2008", "en", "United States", ["Action", "Crime"], ["intense"]),
]


def normalize_title(value):
    return "".join(ch for ch in re.sub(r"[^\w\s]", " ", (value or "").casefold()) if not ch.isspace())


def safe_artwork_url(value):
    """Accept local assets or known HTTPS artwork hosts; reject scripts and arbitrary embeds."""
    from urllib.parse import urlsplit
    candidate = (value or "").strip()[:500]
    if candidate.startswith("/static/") and ".." not in candidate.split("/"):
        return candidate
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    allowed = {"image.tmdb.org", "m.media-amazon.com", "upload.wikimedia.org"}
    return candidate if parsed.scheme == "https" and parsed.hostname in allowed and not parsed.username and not parsed.password else None


def seed_catalog():
    """Idempotently add a small Wikidata-sourced offline starter catalog."""
    added = 0
    for source, external_id, title, year, language, country, genres, moods in STARTER_MOVIES:
        record = CatalogMovie.query.filter_by(source=source, external_id=external_id).first()
        if record:
            continue
        record = CatalogMovie(
            source=source, external_id=external_id, title=title, year=int(year), language=language,
            country=country, genres_json=genres, mood_tags_json=moods,
            provenance_json={
                "identity_url": f"https://www.wikidata.org/wiki/{external_id}",
                "license": "CC0-1.0",
                "classification": "Cineverse editorial tags; verify from Wikidata before relying on factual genre claims",
            },
        )
        db.session.add(record)
        added += 1
    if added:
        db.session.commit()
    return added


def find_local_movies(query="", genre=None, language=None, limit=100):
    rows = CatalogMovie.query.order_by(CatalogMovie.year.desc().nullslast(), CatalogMovie.title.asc()).all()
    needle = normalize_title(query)
    output = []
    for movie in rows:
        aliases = movie.aliases_json or []
        names = [movie.title, movie.original_title or "", *aliases]
        if needle and not any(needle in normalize_title(name) for name in names):
            continue
        if genre and genre.casefold() not in [str(g).casefold() for g in (movie.genres_json or [])]:
            continue
        if language and language.casefold() != (movie.language or "").casefold():
            continue
        output.append(movie)
        if len(output) >= limit:
            break
    return output


def resolve_catalog_title(catalog, title, year=None):
    """Conservative exact title/year resolver suitable for import previews."""
    needle = normalize_title(title)
    candidates = []
    for movie in catalog:
        names = [movie.title, movie.original_title or "", *(movie.aliases_json or [])]
        if needle and any(needle == normalize_title(name) for name in names):
            if year and str(movie.year or "") != str(year):
                continue
            candidates.append(movie)
    if len(candidates) == 1:
        return "matched", candidates[0]
    if len(candidates) > 1:
        return "ambiguous", None
    return "not found", None


def as_card(movie):
    rating_values = movie.ratings_json or {}
    rating_source, rating_raw = next(iter(rating_values.items()), (None, None))
    if isinstance(rating_raw, dict):
        rating_raw = rating_raw.get("value")
    try:
        rating = float(str(rating_raw).split("/")[0]) if rating_raw is not None else None
    except (TypeError, ValueError):
        rating = None
    return {
        "id": movie.id, "title": movie.title, "overview": movie.overview or "",
        "release_date": movie.release_date or (str(movie.year) if movie.year else ""),
        "release_year": movie.year, "genres": movie.genres_json or [], "rating": rating,
        "rating_source": rating_source,
        "poster_url": safe_artwork_url(movie.poster_url), "language": movie.language,
        "is_catalog": True, "movie_key": f"catalog:{movie.id}", "source": movie.source,
    }


class ProviderError(RuntimeError):
    pass


class WikidataProvider:
    """Small scoped reads for known Wikidata items; never used by a web request."""
    def __init__(self, session=None, timeout=(2, 6)):
        self.session = session or requests.Session()
        self.timeout = timeout

    def entity(self, entity_id):
        if not re.fullmatch(r"Q[1-9]\d*", entity_id or ""):
            raise ValueError("A Wikidata entity ID such as Q42 is required.")
        try:
            response = self.session.get(
                f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json",
                headers={"User-Agent": "CineverseStudentProject/1.0 (metadata lookup; contact: local operator)",
                         "Accept-Encoding": "gzip,deflate"},
                timeout=self.timeout,
            )
            if response.status_code == 429:
                raise ProviderError("Wikidata asked Cineverse to slow down; retry later.")
            response.raise_for_status()
            payload = response.json()
        except ProviderError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError("Wikidata could not be reached or returned invalid JSON.") from exc
        entity = (payload.get("entities") or {}).get(entity_id)
        if not entity or entity.get("missing") is not None:
            raise ProviderError("Wikidata item was not found.")
        claims = entity.get("claims") or {}
        def claim_ids(property_id):
            return [claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                    for claim in claims.get(property_id, [])
                    if isinstance(claim.get("mainsnak", {}).get("datavalue", {}).get("value"), dict)]
        dates = []
        for claim in claims.get("P577", []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time")
            if isinstance(value, str) and re.match(r"^[+-]?\d{4}-\d\d-\d\d", value):
                dates.append(value.lstrip("+")[:10])
        return {
            "source": "wikidata", "external_id": entity_id,
            "title": (entity.get("labels", {}).get("en") or {}).get("value"),
            "aliases": [item.get("value") for item in entity.get("aliases", {}).get("en", []) if item.get("value")],
            "description": (entity.get("descriptions", {}).get("en") or {}).get("value"),
            "release_dates": dates, "language_ids": claim_ids("P364"),
            "country_ids": claim_ids("P495"), "cast_ids": claim_ids("P161"),
            "provenance": {"provider": "Wikidata", "license": "CC0-1.0", "item_url": f"https://www.wikidata.org/wiki/{entity_id}"},
        }


class OMDbProvider:
    """Optional user-configured OMDb adapter. Never called during page rendering."""
    endpoint = "https://www.omdbapi.com/"
    daily_limit = 1000

    def __init__(self, api_key=None, session=None, timeout=(2, 5), sleeper=time.sleep):
        self.api_key = api_key if api_key is not None else os.getenv("OMDB_API_KEY", "")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.sleeper = sleeper
        self._quota_day = date.today()
        self._requests_today = 0

    @property
    def configured(self):
        return bool(self.api_key.strip())

    def _request(self, **params):
        if not self.configured:
            raise ProviderError("OMDb is not configured; local movie discovery remains available.")
        params["apikey"] = self.api_key
        for attempt in range(2):
            if date.today() != self._quota_day:
                self._quota_day, self._requests_today = date.today(), 0
            if self._requests_today >= self.daily_limit:
                raise ProviderError("OMDb daily request limit reached.")
            try:
                self._requests_today += 1
                response = self.session.get(self.endpoint, params=params, timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == 0:
                    self.sleeper(0.15)
                    continue
                raise ProviderError("OMDb could not be reached.") from exc
            if payload.get("Response") == "False":
                raise ProviderError(payload.get("Error", "OMDb returned no result."))
            return payload
        raise ProviderError("OMDb could not be reached.")

    def search(self, title, year=None, page=1):
        if not 1 <= int(page) <= 100:
            raise ValueError("OMDb search page must be between 1 and 100.")
        payload = self._request(s=title, y=year, page=int(page), type="movie")
        return [self.normalize(item) for item in payload.get("Search", [])]

    def details(self, imdb_id):
        if not re.fullmatch(r"tt\d{7,10}", imdb_id or ""):
            raise ValueError("A verified IMDb identifier is required.")
        return self.normalize(self._request(i=imdb_id, plot="full"))

    @staticmethod
    def normalize(item):
        def split_names(value):
            return [part.strip() for part in (value or "").split(",") if part.strip() and part.strip() != "N/A"]
        ratings = {}
        for rating in item.get("Ratings", []):
            source, value = rating.get("Source"), rating.get("Value")
            if source and value:
                ratings[source] = {"value": value, "source": source}
        poster = item.get("Poster")
        return {
            "source": "omdb", "external_id": item.get("imdbID"), "imdb_id": item.get("imdbID"),
            "title": item.get("Title"), "year": int(item["Year"][:4]) if (item.get("Year") or "")[:4].isdigit() else None,
            "release_date": item.get("Released") if item.get("Released") != "N/A" else None,
            "runtime_minutes": int(item["Runtime"].split()[0]) if (item.get("Runtime") or "").split() and item["Runtime"].split()[0].isdigit() else None,
            "genres": split_names(item.get("Genre")), "language": item.get("Language"),
            "country": item.get("Country"),
            "overview": item.get("Plot") if item.get("Plot") != "N/A" else None,
            "cast": split_names(item.get("Actors")), "directors": split_names(item.get("Director")),
            "writers": split_names(item.get("Writer")), "poster_url": poster if poster and poster != "N/A" else None,
            "ratings": ratings,
            "provenance": {"provider": "OMDb", "license": "CC BY-NC 4.0", "retrieved_at": datetime.now(timezone.utc).isoformat()},
        }
