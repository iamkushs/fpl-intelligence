"""Alembic version-table compatibility helpers.

Alembic's default ``version_num`` column is ``VARCHAR(32)``, while this
project deliberately uses descriptive revision identifiers that can be longer.
"""

from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from alembic.ddl.impl import DefaultImpl
from sqlalchemy.engine import Connection


ALEMBIC_VERSION_ID_LENGTH = 128


def configure_version_table() -> None:
    """Make Alembic create its version table with project-safe capacity."""
    if getattr(DefaultImpl, "_fpl_version_table_configured", False):
        return

    original: Callable[..., sa.Table] = DefaultImpl.version_table_impl

    def version_table_impl(self: DefaultImpl, **kwargs: Any) -> sa.Table:
        table = original(self, **kwargs)
        table.c.version_num.type = sa.String(ALEMBIC_VERSION_ID_LENGTH)
        return table

    DefaultImpl.version_table_impl = version_table_impl
    DefaultImpl._fpl_version_table_configured = True


def ensure_postgresql_version_table_capacity(
    connection: Connection, version_table: str, version_table_schema: str | None = None
) -> None:
    """Widen a legacy PostgreSQL Alembic version column before it is updated."""
    if connection.dialect.name != "postgresql":
        return

    inspector = sa.inspect(connection)
    if not inspector.has_table(version_table, schema=version_table_schema):
        return

    column = next(
        (
            item
            for item in inspector.get_columns(version_table, schema=version_table_schema)
            if item["name"] == "version_num"
        ),
        None,
    )
    if column is None or getattr(column["type"], "length", None) is None:
        return
    if column["type"].length >= ALEMBIC_VERSION_ID_LENGTH:
        return

    quote = connection.dialect.identifier_preparer.quote
    qualified_table = quote(version_table)
    if version_table_schema:
        qualified_table = f"{quote(version_table_schema)}.{qualified_table}"
    connection.execute(
        sa.text(
            f"ALTER TABLE {qualified_table} ALTER COLUMN {quote('version_num')} "
            f"TYPE VARCHAR({ALEMBIC_VERSION_ID_LENGTH})"
        )
    )
