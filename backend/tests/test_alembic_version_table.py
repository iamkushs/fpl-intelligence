from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from fpl_intelligence.db import migration_version_table
from fpl_intelligence.integrations.fpl.bootstrap import FPLBootstrapSyncService
from fpl_intelligence.integrations.fpl.schemas import FPLBootstrap, FPLClub, FPLGameweek, FPLPlayer


def test_all_revision_ids_fit_project_version_table_capacity():
    revisions = []
    for path in Path("alembic/versions").glob("*.py"):
        match = re.search(r'^revision\s*=\s*"([^"]+)"', path.read_text(), re.MULTILINE)
        assert match, f"Missing revision identifier in {path}"
        revisions.append(match.group(1))

    assert max(map(len, revisions)) <= migration_version_table.ALEMBIC_VERSION_ID_LENGTH


def test_version_table_override_creates_wide_column_on_empty_database():
    migration_version_table.configure_version_table()
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            context._ensure_version_table()
            column = sa.inspect(connection).get_columns("alembic_version")[0]
    finally:
        engine.dispose()

    assert column["name"] == "version_num"
    assert column["type"].length == migration_version_table.ALEMBIC_VERSION_ID_LENGTH


def test_postgresql_legacy_version_table_is_widened_idempotently(monkeypatch):
    dialect = sa.dialects.postgresql.dialect()

    class Connection:
        def __init__(self, length):
            self.dialect = dialect
            self.length = length
            self.statements = []

        def execute(self, statement):
            self.statements.append(str(statement))

    class Inspector:
        def __init__(self, connection):
            self.connection = connection

        def has_table(self, name, schema=None):
            return True

        def get_columns(self, name, schema=None):
            return [{"name": "version_num", "type": sa.String(self.connection.length)}]

    legacy = Connection(32)
    monkeypatch.setattr(migration_version_table.sa, "inspect", lambda connection: Inspector(connection))
    migration_version_table.ensure_postgresql_version_table_capacity(legacy, "alembic_version")

    assert legacy.statements == [
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"
    ]

    already_wide = Connection(128)
    migration_version_table.ensure_postgresql_version_table_capacity(already_wide, "alembic_version")
    assert already_wide.statements == []


def test_empty_sqlite_database_upgrades_to_head(tmp_path, monkeypatch):
    database_path = tmp_path / "migrations.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = sa.create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0020_canonical_fpl_bootstrap"
            )
            inspector = sa.inspect(connection)
            assert {"fpl_clubs", "fpl_gameweeks", "players"} <= set(inspector.get_table_names())
            assert "club_id" in {column["name"] for column in inspector.get_columns("players")}
            assert "ix_players_club_id" in {index["name"] for index in inspector.get_indexes("players")}

        class Adapter:
            def get_bootstrap(self):
                return FPLBootstrap(
                    clubs=[FPLClub(id=1, name="Example FC", short_name="EXA")],
                    gameweeks=[FPLGameweek(number=1, name="Gameweek 1", is_next=True)],
                    players=[FPLPlayer(id=10, first_name="Alex", second_name="Example", display_name="A Example", club_id=1, club_name="Example FC", club_short_name="EXA", position="DEF", price=4.5, ownership_percent=12.3, availability_status="a")],
                )

        session = sessionmaker(bind=engine)()
        service = FPLBootstrapSyncService(Adapter())
        assert service.sync(session).players == 1
        assert service.sync(session).players == 1
        assert session.execute(sa.text("SELECT count(*) FROM fpl_clubs")).scalar_one() == 1
        session.close()
    finally:
        engine.dispose()
