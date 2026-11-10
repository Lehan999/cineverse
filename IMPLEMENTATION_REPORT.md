# Cineverse implementation report

Updated 2026-11-08. The work is in the working tree only; no commit, push, or Git history rewrite was made. The available checkout is the local `main` branch at the prior audit revision `420f4ec`. Network DNS prevented checking whether GitHub `main` has moved since then.

## What changed

- **Local-first discovery:** Homepage, search, and genre shelves now use the local catalog. Rendering those pages no longer calls TMDB or downloads poster files. Hero content comes from catalog data, not hardcoded Ramayana/Avengers searches. Legacy TMDB cache rows remain in the database and old TMDB references resolve through exact cached IDs where possible. On 2026-11-10, 43 legacy poster assets were restored and 57 catalog posters were cached locally; 98/109 catalog records now have verified local poster artwork. The Genres page hides only the Avengers Endgame card while preserving its record.
- **Provider-independent records:** Added a canonical `CatalogMovie` record with source/external identity, IMDb ID, years, summary, genres, runtime, language/country, poster/artwork URLs, source-attributed ratings, cast/crew, aliases, editorial mood labels, provenance and fetch time. Local IDs back new catalog routes; existing `Movie`, favorite, review and cache IDs stay preserved.
- **Providers:** Added optional OMDb search/details normalization and a scoped Wikidata entity adapter. Both have timeouts/error handling; OMDb has an in-process daily call guard. A deliberate refresh script stores explicit provenance. No API key was available, so no live OMDb call is claimed. Wikidata's pages were verified in the browser; the Python client was tested only with mocks.
- **First-party product features:** Added local CineMatch, roulette, CineDuel preferences, Cinema DNA activity summaries, invite-code CineRoom with private votes and owner reveal, watchlist/watched status, private diary notes, custom lists, CSV export and preview/import/undo. Reviews can be edited with history, spoiler-marked paragraphs, helpful votes and user reports. Moderators configured by account ID can hide/restore reported reviews.
- **Security and reliability:** Kept password hashing and Flask-WTF CSRF, added a per-IP rolling sign-in throttle, conditional secure session-cookie setting, conservative import resolution, server-side movie metadata resolution for new catalog actions, and guarded LocalStorage access in JavaScript.
- **Interface:** Replaced the accumulated override stylesheet with a compact dark/light token system, responsive layouts, keyboard focus states, reduced-motion behavior and CSS poster fallbacks. No runtime CDN or frontend build dependency was added.
- **Database:** Added schema version tracking and additive SQLite migrations, with a verified ignored backup before upgrades of a real file database. Exact legacy local/TMDB identifiers are copied into canonical catalog records and linked; ambiguous records are retained without title-based guessing.
- **Documentation:** Added `DATA_SOURCES.md`, `COMPETITOR_COMPARISON.md`, updated the existing README and added this report.

## Confirmed issues and status

| Finding | Status |
|---|---|
| Homepage made multiple serial TMDB requests for popular and featured titles, then poster downloads inside the request | Removed from homepage rendering; it now reads SQLite only. |
| Genre page called TMDB for genres and multiple rows and had no offline fallback | Replaced with local genre groups. |
| New movie identifier scheme needed query flags to distinguish local/TMDB IDs | New provider-neutral `/film/<catalog_id>` routes added; old route compatibility retained for cached records. |
| Favorite and review forms trusted hidden movie title fields | New catalog actions resolve the movie from the DB. Legacy TMDB favorite/review actions now require an exact cached catalog record. |
| Reviews had no spoiler controls, edit history, voting/reporting or moderator action | Implemented; tested in the feature suite. |
| No sign-in attempt throttle | Added DB-backed per-IP window throttle (8 attempts / 15 minutes). This is single-instance protection, not a distributed rate-limit service. |
| CSS had roughly 1,400 lines of accumulated overrides | Replaced by one cohesive stylesheet and responsive tokens. |
| README startup port was inaccurate | Updated: `run.py` chooses 5001–5010. |
| SQLite database tracked publicly by Git | Actual local database remains in place and is ignored. Its deletion is staged from the current Git index only. Existing public history still contains the old database blob. |
| Tracked bytecode exposure noted in audit | The three tracked `.pyc` files are staged for removal from the index and ignored going forward; working copies may be recreated locally by Python. |

## Database exposure and owner action

The tracked SQLite database was read using a read-only query. It contained one account row, one favorite, one review, one legacy movie and 79 TMDB cache rows. No email, password hash, review text, or credential was printed here. A verified local backup was created before schema changes at `instance/backups/Cineverse-pre-migration-20261105.sqlite3` during the prior audit; migration upgrades also create timestamped backups. The working database remains at `instance/Cineverse.db` and was not deleted.

Because the database was committed to public Git, removing it from the current index does not erase it from prior commits or clones. The repository owner must decide whether to rotate/review account credentials and use a deliberate history-purge process; history rewriting was not authorized and was not performed. Treat the already exposed password hash and account metadata as compromised. Do not publish the local backup.

## Movie test results and limits

The offline test fixture contains 45 real film-title strings across Telugu, Hindi and English, including alternative spellings and two `Dune` releases. The local resolver test matched **45/45 fixture rows** on exact normalized title/year/language and correctly marked `Dune` ambiguous without a year. This only verifies the local resolver against its own curated test fixture; it is **not** a 45-title provider identification benchmark.

No live provider data were loaded for these 45 test rows. Fixture tests intentionally show overview, cast, poster and ratings availability as **0/45** rather than inventing completeness. The 11 seeded production identities now have locally cached poster art; plot, cast, ratings and streaming data remain incomplete. Eleven other catalog records have no poster path in their TMDB detail response. Mood/genre tags are labeled as Cineverse editorial labels in provenance and should not be mistaken for verified source metadata. Search aliases and imported records are conservative; ambiguous title matching is not guessed.

## Competitor comparison and measured advantages

Seven reference projects were reviewed using GitHub READMEs, visible repository trees, and published license information. Comparative patterns and limitations are in `COMPETITOR_COMPARISON.md`. The research did not perform a full line-by-line source-code audit of each external project because shell GitHub access failed on DNS; claims are scoped to material that was actually inspected. No code/assets were copied.

The precise advantage currently supported by code inspection is **offline page independence**: home/search/genres/CineMatch/roulette make zero metadata-provider requests during page handling. Browser network instrumentation was not run. Recommendation time, mobile screenshots, assistive-technology behavior, multi-user group quality, and import accuracy beyond unit tests remain unmeasured; this project does not claim global or catalog superiority over IMDb, Letterboxd, Trakt, JustWatch, or the reference projects.

## Automated verification

Run in the project virtual environment:

```powershell
python -m compileall -q app tests run.py
python -m unittest discover -s tests -v
```

Final run used `.venv\Scripts\python.exe`: compilation succeeded; `python -m unittest discover -s tests -v` ran **40 tests, all passed**. The suite includes mocked provider normalization/errors/quota, the 45-title offline resolver set, local home/search/genre rendering, auth/password/CSRF flows, local reviews/favorites, spoiler/history/helpful/report flows, CSV preview/undo, diary privacy, CineDuel/CineRoom/Cinema DNA workflows, additive migration preservation, safe artwork host validation, and the exact Avengers Endgame genre filter. A real local Flask test-client smoke check returned HTTP 200 for `/`, `/genres`, `/search?q=matrix`, `/cine-match`, and `/roulette`. Live TMDB detail/image retrieval stored 100 poster files, including verified matches for the 11 seed titles; 11 additional records have no poster in their current TMDB detail responses. No browser visual check has been run.

## Files changed/added

- Existing app: `.gitignore`, `app/__init__.py`, `app/models.py`, `app/routes.py`, `app/static/css/style.css`, `app/static/js/main.js`, existing templates for base/home/genres/movie/account/favorites.
- New app modules: `app/catalog.py`, `app/migrations.py`.
- New templates: CineMatch, CineDuel, CineRoom, Cinema DNA, roulette, watchlist, custom lists, CSV import, moderation.
- Tests: updated `tests/test_tmdb.py`; added `tests/test_catalog_coverage.py`, `tests/test_features.py`, `tests/test_providers.py`.
- Docs/maintenance: `README`, `DATA_SOURCES.md`, `COMPETITOR_COMPARISON.md`, `IMPLEMENTATION_REPORT.md`, `scripts/refresh_catalog.py`.
- Git index: staged removal of `instance/Cineverse.db`; local file preserved and ignored.

## Incomplete or requiring owner action

- Set `FLASK_SECRET_KEY` and, only if permitted for your use, `OMDB_API_KEY` in the private `.env`. OMDb's source terms/license need owner review for any public use; the free plan is non-commercial. No live integration result is claimed.
- Confirm current TMDB retention and attribution terms for legacy cache and previously generated images. Existing cache data/files were preserved, not refreshed or deleted.
- Verify India coverage and terms with Watchmode before adding streaming information; it is not wired in. Trakt is not a dependency or integration.
- Expand the starter catalog with source-verified identity fields and licensed factual metadata/artwork. No Wikidata/Wikimedia poster imagery, plots or cast were copied into this build.
- The OMDb refresher is a single-title maintenance utility, not a background job scheduler or persistent cross-process quota ledger. A scheduled worker, provider-locking, cross-process quotas, and cache retention cleanup remain future work.
- CineRoom uses one vote per browser session, not named verified participants; code invite links are not expiring and there is no realtime update. Room expiry/capacity and abuse controls need further hardening before public deployment.
- CSV import supports a narrow title/year/watched format and imports watch status only. It does not import third-party review/rating histories, nor offer a transaction-wide import rollback after unrelated subsequent edits.
- Review moderation has a simple configured-ID queue, not a full moderation policy, appeal flow, or automated abuse defense. Helpful vote ordering is local and should be performance-tested with larger data.
- Custom lists and account export/import are basic; CSV export currently centers on reviews/watch entries and is not a complete portable archive for every account table.
- No browser screenshots at phone/tablet sizes, accessibility audit, or latency benchmark was captured.

## Run locally

In PowerShell: activate `.venv`, then `python run.py`; use the URL printed by the launcher. See the `README` for full first-run and environment setup.
