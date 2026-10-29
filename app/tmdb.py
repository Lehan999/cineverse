"""Small TMDB API client used by the Flask routes."""

import os

import requests


API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TIMEOUT_SECONDS = 8


class TMDBError(RuntimeError):
    """A safe, user-facing TMDB error that never includes credentials."""


def _get(endpoint, params=None):
    read_token = os.getenv("TMDB_READ_ACCESS_TOKEN")
    api_key = os.getenv("TMDB_API_KEY")
    if not read_token and not api_key:
        raise TMDBError("TMDB credentials are not configured. Set TMDB_API_KEY in your .env file.")

    request_params = dict(params or {})
    headers = {"accept": "application/json"}
    if read_token:
        headers["Authorization"] = f"Bearer {read_token}"
    else:
        request_params["api_key"] = api_key

    try:
        response = requests.get(
            f"{API_BASE}/{endpoint.lstrip('/')}",
            params=request_params,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except requests.ConnectTimeout:
        raise TMDBError("Python could not connect to TMDB over HTTPS. Check network access or proxy settings.") from None
    except requests.ReadTimeout:
        raise TMDBError("TMDB did not respond in time. Please try again shortly.") from None
    except requests.Timeout:
        raise TMDBError("The TMDB request timed out. Please try again shortly.") from None
    except requests.ConnectionError as exc:
        raise TMDBError("Could not connect to TMDB. Check your internet connection and try again.") from None
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        if status in (401, 403):
            raise TMDBError("TMDB rejected the credentials. Check the API key or read access token in .env.") from None
        raise TMDBError(f"TMDB returned an error (HTTP {status}). Please try again later.") from None
    except (requests.RequestException, ValueError):
        raise TMDBError("TMDB returned an unreadable response. Please try again later.") from None


def image_url(path):
    return f"{IMAGE_BASE}{path}" if path else None


def _genre_names(genre_ids):
    """Resolve common genre IDs without a second network request."""
    names = {
        28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
        80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
        14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
        9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
        10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    }
    return [names[genre_id] for genre_id in genre_ids if genre_id in names]


def _map_movie(item):
    release_date = item.get("release_date") or ""
    return {
        "id": item["id"],
        "title": item.get("title") or "Untitled",
        "poster_url": image_url(item.get("poster_path")),
        "genres": _genre_names(item.get("genre_ids", [])),
        "rating": item.get("vote_average"),
        "release_date": release_date,
        "release_year": release_date[:4],
        "overview": item.get("overview") or "No description is available.",
    }


def popular_movies(page=1):
    result = _get("movie/popular", {"language": "en-US", "page": page})
    return [_map_movie(item) for item in result.get("results", [])]


def search_movies(query, page=1):
    result = _get("search/movie", {"query": query, "language": "en-US", "page": page})
    return [_map_movie(item) for item in result.get("results", [])]


def movie_genres():
    """Return TMDB's current movie genre catalog."""
    result = _get("genre/movie/list", {"language": "en-US"})
    return [
        {"id": genre["id"], "name": genre["name"]}
        for genre in result.get("genres", [])
        if genre.get("id") is not None and genre.get("name")
    ]


def movies_by_genre(genre_id, page=1):
    """Return popular TMDB titles in one genre."""
    result = _get(
        "discover/movie",
        {
            "language": "en-US",
            "with_genres": genre_id,
            "sort_by": "popularity.desc",
            "page": page,
        },
    )
    return [_map_movie(item) for item in result.get("results", [])]


def movie_details(movie_id):
    movie = _get(
        f"movie/{movie_id}",
        {"language": "en-US", "append_to_response": "credits,videos"},
    )
    credits = movie.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    notable_jobs = {
        "Producer", "Executive Producer", "Writer", "Screenplay", "Director of Photography",
        "Original Music Composer", "Editor", "Production Design", "Casting",
    }
    notable_crew = [person for person in crew if person.get("job") in notable_jobs]
    videos = (movie.get("videos") or {}).get("results") or []
    trailer = next(
        (video for video in videos if video.get("site") == "YouTube" and video.get("type") == "Trailer"),
        None,
    )
    return {
        "id": movie["id"],
        "title": movie.get("title") or "Untitled",
        "poster_url": image_url(movie.get("poster_path")),
        "backdrop_url": image_url(movie.get("backdrop_path")),
        "genres": [genre.get("name") for genre in movie.get("genres", []) if genre.get("name")],
        "rating": movie.get("vote_average"),
        "release_date": movie.get("release_date") or "",
        "release_year": (movie.get("release_date") or "")[:4],
        "runtime": movie.get("runtime"),
        "overview": movie.get("overview") or "No description is available.",
        "cast": [
            {"name": person.get("name"), "character": person.get("character"), "photo_url": image_url(person.get("profile_path"))}
            for person in cast[:12]
        ],
        "directors": [person.get("name") for person in crew if person.get("job") == "Director" and person.get("name")],
        "crew": [
            {
                "name": person.get("name"),
                "job": person.get("job"),
                "department": person.get("department"),
                "photo_url": image_url(person.get("profile_path")),
            }
            for person in notable_crew[:20]
        ],
        "trailer_url": f"https://www.youtube.com/watch?v={trailer['key']}" if trailer and trailer.get("key") else None,
    }

