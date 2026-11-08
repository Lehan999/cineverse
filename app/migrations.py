"""Small, ordered, additive SQLite migrations for this single-instance Flask app.

Run before any schema changes. Every migration is idempotent, recorded in
schema_migrations, and preceded by a verified local backup for file databases.
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from flask import current_app
from sqlalchemy import inspect, text

from app import db


def _backup_file_database():
    if current_app.testing:
        return None
    url = db.engine.url
    if url.get_backend_name() != "sqlite" or url.database in (None, ":memory:"):
        return None
    source = Path(url.database)
    if not source.is_absolute():
        source = Path(current_app.instance_path) / source
    if not source.exists():
        return None
    backup_dir = Path(current_app.instance_path) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{source.stem}-before-migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    with sqlite3.connect(source) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    with sqlite3.connect(backup) as verify:
        verify.execute("PRAGMA quick_check").fetchone()
    return backup


def upgrade_schema():
    """Create missing tables, then apply recorded additive migrations."""
    inspector = inspect(db.engine)
    has_version_table = inspector.has_table("schema_migrations")
    migration_pending = not has_version_table
    if has_version_table:
        with db.engine.connect() as conn:
            current = conn.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar() or 0
            migration_pending = current < 4
    if migration_pending:
        _backup_file_database()
    # create_all is a fresh-install baseline only; versioned migrations below
    # handle changes to existing tables and record the active schema version.
    db.create_all()
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"))
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
        if 1 not in applied:
            columns = {col["name"] for col in inspect(db.engine).get_columns("review")}
            if "spoiler_paragraphs_json" not in columns:
                conn.execute(text("ALTER TABLE review ADD COLUMN spoiler_paragraphs_json JSON NOT NULL DEFAULT '[]'"))
            conn.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (1, :now)"), {"now": datetime.now(timezone.utc).isoformat()})
            applied.add(1)
        if 2 not in applied:
            for table in ("review", "favorite"):
                columns = {col["name"] for col in inspect(db.engine).get_columns(table)}
                if "catalog_movie_id" not in columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN catalog_movie_id INTEGER REFERENCES catalog_movie(id) ON DELETE SET NULL"))
            conn.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (2, :now)"), {"now": datetime.now(timezone.utc).isoformat()})
            applied.add(2)
        if 3 not in applied:
            columns = {col["name"] for col in inspect(db.engine).get_columns("review")}
            if "is_hidden" not in columns:
                conn.execute(text("ALTER TABLE review ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT 0"))
            report_columns = {col["name"] for col in inspect(db.engine).get_columns("review_report")}
            if "status" not in report_columns:
                conn.execute(text("ALTER TABLE review_report ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'open'"))
            conn.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (3, :now)"), {"now": datetime.now(timezone.utc).isoformat()})
            applied.add(3)
        if 4 not in applied:
            columns = {col["name"] for col in inspect(db.engine).get_columns("cine_room")}
            if "is_revealed" not in columns:
                conn.execute(text("ALTER TABLE cine_room ADD COLUMN is_revealed BOOLEAN NOT NULL DEFAULT 0"))
            conn.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (4, :now)"), {"now": datetime.now(timezone.utc).isoformat()})
    _migrate_legacy_movie_records()


def _migrate_legacy_movie_records():
    """Copy exact legacy identities into canonical rows and link user rows without deleting originals."""
    from app.models import CatalogMovie, Favorite, Movie, Review, TMDBCacheEntry

    for legacy in Movie.query.all():
        record = CatalogMovie.query.filter_by(source="legacy", external_id=str(legacy.id)).first()
        if record is None:
            record = CatalogMovie(source="legacy", external_id=str(legacy.id), title=legacy.title,
                year=legacy.release_year, overview=legacy.description,
                genres_json=[part.strip() for part in (legacy.genre or "").split(",") if part.strip()],
                language=legacy.language,
                runtime_minutes=int(legacy.duration.split()[0]) if legacy.duration and legacy.duration.split()[0].isdigit() else None,
                poster_url=f"/static/{legacy.poster.lstrip('/')}" if legacy.poster else None,
                ratings_json={"IMDb": {"value": legacy.imdb_rating, "source": "IMDb", "provenance": "legacy database field"}} if legacy.imdb_rating is not None else {},
                provenance_json={"source": "preserved legacy movie row", "license": "unknown; existing local content retained"})
            db.session.add(record)
    db.session.flush()
    for entry in TMDBCacheEntry.query.filter(TMDBCacheEntry.cache_key.like("movie:%")).all():
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        ext_id = str(payload.get("id") or entry.cache_key.partition(":")[2])
        if not ext_id.isdigit() or not payload.get("title"):
            continue
        record = CatalogMovie.query.filter_by(source="tmdb-legacy", external_id=ext_id).first()
        if record is None:
            release = payload.get("release_date") or ""
            rating = payload.get("rating")
            record = CatalogMovie(source="tmdb-legacy", external_id=ext_id, title=payload["title"],
                year=int(release[:4]) if release[:4].isdigit() else None, release_date=release,
                overview=payload.get("overview"), genres_json=payload.get("genres") or [],
                poster_url=payload.get("poster_url"), ratings_json={"TMDB": {"value": rating, "source": "TMDB", "provenance": "preserved legacy cache"}} if rating is not None else {},
                cast_json=payload.get("cast") or [], crew_json=payload.get("crew") or [],
                provenance_json={"source": "preserved TMDB cache", "license": "legacy cache retained; current terms review required"})
            db.session.add(record)
    db.session.flush()
    for model in (Review, Favorite):
        for user_movie in model.query.filter(model.catalog_movie_id.is_(None)).all():
            prefix, _, legacy_id = user_movie.movie_key.partition(":")
            source = "legacy" if prefix == "local" else "tmdb-legacy" if prefix == "tmdb" else None
            if source and legacy_id.isdigit():
                record = CatalogMovie.query.filter_by(source=source, external_id=legacy_id).first()
                if record:
                    user_movie.catalog_movie_id = record.id
    db.session.commit()

