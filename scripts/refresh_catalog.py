"""Explicitly refresh one OMDb title into the local catalog.

Usage: python scripts/refresh_catalog.py --title "The Matrix" --year 1999
Requires OMDB_API_KEY. This is never run as part of normal page rendering.
"""
import argparse

from app import create_app, db
from app.catalog import OMDbProvider
from app.models import CatalogMovie


def main():
    parser = argparse.ArgumentParser(description="Refresh one title from OMDb into Cineverse's local catalog.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--confirm-noncommercial", action="store_true", help="Confirm your OMDb use complies with its current non-commercial license.")
    args = parser.parse_args()
    if not args.confirm_noncommercial:
        parser.error("OMDb data is CC BY-NC 4.0; pass --confirm-noncommercial only for compliant non-commercial use.")
    app = create_app()
    with app.app_context():
        provider = OMDbProvider()
        results = provider.search(args.title, year=args.year)
        if not results:
            raise SystemExit("OMDb returned no search results.")
        exact = [item for item in results if item.get("title", "").casefold() == args.title.casefold() and (not args.year or item.get("year") == args.year)]
        candidates = exact or results
        if len(candidates) != 1:
            print("Ambiguous matches; refine the title/year using OMDb's search results before updating a record:")
            for item in candidates[:10]:
                print(f"- {item.get('title')} ({item.get('year') or 'year unavailable'}) — {item.get('imdb_id') or 'no IMDb ID'}")
            raise SystemExit(2)
        result = candidates[0]
        if result.get("imdb_id"):
            result = provider.details(result["imdb_id"])
        if not result.get("external_id"):
            raise SystemExit("OMDb result has no verified IMDb identifier; no catalog record was changed.")
        record = CatalogMovie.query.filter_by(source="omdb", external_id=result["external_id"]).first()
        if record is None:
            record = CatalogMovie(source="omdb", external_id=result["external_id"])
            db.session.add(record)
        record.imdb_id = result.get("imdb_id")
        record.title = result.get("title") or args.title
        record.year = result.get("year")
        record.release_date = result.get("release_date")
        record.overview = result.get("overview")
        record.genres_json = result.get("genres") or []
        record.language = result.get("language")
        record.country = result.get("country")
        record.runtime_minutes = result.get("runtime_minutes")
        record.poster_url = result.get("poster_url")
        record.cast_json = [{"name": name} for name in result.get("cast", [])]
        record.crew_json = ([{"name": name, "job": "Director"} for name in result.get("directors", [])]
                            + [{"name": name, "job": "Writer"} for name in result.get("writers", [])])
        record.ratings_json = result.get("ratings") or {}
        record.provenance_json = result.get("provenance") or {"provider": "OMDb", "license": "CC BY-NC 4.0"}
        db.session.commit()
        print(f"Saved {record.title} ({record.year or 'year unavailable'}) to the local catalog.")


if __name__ == "__main__":
    main()
