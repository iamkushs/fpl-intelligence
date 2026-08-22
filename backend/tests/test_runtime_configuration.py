from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.config import Settings, normalize_database_url
from fpl_intelligence.db.session import Database


def test_neon_postgresql_url_uses_installed_psycopg_driver_and_keeps_tls_query():
    url = "postgresql://user:password@ep-example.neon.tech/neondb?sslmode=require"

    assert normalize_database_url(url) == "postgresql+psycopg://user:password@ep-example.neon.tech/neondb?sslmode=require"
    database = Database(Settings(database_url=url))
    try:
        assert database.engine.url.drivername == "postgresql+psycopg"
        assert database.engine.url.query["sslmode"] == "require"
    finally:
        database.engine.dispose()


def test_health_and_local_cors_work_without_database_or_research_credentials():
    app = create_app(Settings(database_url="", cors_origins="http://localhost:3000"))

    with TestClient(app) as client:
        health = client.get("/health")
        cors = client.options("/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})

    assert health.json() == {"status": "ok"}
    assert cors.headers["access-control-allow-origin"] == "http://localhost:3000"
