"""SQLAlchemy engine and session setup."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fpl_intelligence.config import Settings, get_settings


class Database:
    """Application-owned SQLAlchemy database resources."""

    def __init__(self, settings: Settings):
        url = settings.require_database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {"connect_timeout": 10}
        engine_kwargs = {"future": True, "pool_pre_ping": True, "connect_args": connect_args}
        if not url.startswith("sqlite"):
            engine_kwargs["pool_timeout"] = 10
        self.engine = create_engine(url, **engine_kwargs)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()


def get_database() -> Database:
    return Database(get_settings())
