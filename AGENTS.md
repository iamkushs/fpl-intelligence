# Repository operating guide

- The FastAPI/SQLAlchemy backend is under `backend/fpl_intelligence`; backend tests are under `backend/tests`.
- The Next.js frontend is under `frontend`; its checks are defined in `frontend/package.json`.
- Alembic configuration is in `alembic.ini`, with migrations under `alembic/versions`. Preserve migration history; add forward migrations rather than rewriting applied revisions.
- PostgreSQL is the production database convention. Tests and verification use isolated SQLite databases and must never use production credentials.
- The canonical Player ID is the integer ID supplied by the official FPL API. Reuse it consistently across persistence, APIs, and UI routes.
- Research and application state belong in PostgreSQL/database persistence, not generated Markdown files. Temporary research output is allowed only when ignored and uncommitted.
- Inspect existing code before adding abstractions. Reuse the current architecture and do not create duplicate or competing subsystems.
- Add or update tests for changed behavior. Run `./scripts/verify-all.sh` before completion and inspect the resulting diff.
- The GitHub issue title and body define the current task; the issue body is its acceptance criteria. Implement only that issue, not requirements from future issues.
- Existing local Markdown files other than root WORKFLOW.md and root AGENTS.md are legacy/local-only material and must not be used as implementation requirements or committed.
