"""Small production-safe operational commands."""

import argparse

from fpl_intelligence.config import get_settings
from fpl_intelligence.db.session import Database
from fpl_intelligence.integrations.fpl.adapter import OfficialFPLAdapter
from fpl_intelligence.integrations.fpl.bootstrap import FPLBootstrapSyncService


def sync_fpl_bootstrap() -> None:
    settings = get_settings()
    database = Database(settings)
    try:
        with database.session_factory() as session:
            result = FPLBootstrapSyncService(OfficialFPLAdapter(
                base_url=settings.official_fpl_base_url,
                timeout_seconds=settings.official_fpl_timeout_seconds,
            )).sync(session)
        print(f"Canonical FPL bootstrap synced: clubs={result.clubs} gameweeks={result.gameweeks} players={result.players}")
    finally:
        database.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="FPL Intelligence operations")
    parser.add_argument("command", choices=["sync-fpl-bootstrap"])
    args = parser.parse_args()
    if args.command == "sync-fpl-bootstrap":
        sync_fpl_bootstrap()


if __name__ == "__main__":
    main()
