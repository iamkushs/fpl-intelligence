from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database

def test_cycle_api_create_prepare_get_and_latest():
    url=f"sqlite:///./cycle_api_{uuid4().hex}.db"; settings=Settings(database_url=url);database=Database(settings);Base.metadata.create_all(database.engine)
    app=create_app(settings,database=database,codex_service=CodexService(client=object()))
    try:
        with TestClient(app) as client:
            created=client.post("/research/cycles",json={"gameweek":5,"research_cutoff":datetime.now(timezone.utc).isoformat()});assert created.status_code==201,created.text
            cycle=created.json();prepared=client.post(f"/research/cycles/{cycle['id']}/prepare");assert prepared.status_code==200,prepared.text
            assert prepared.json()["summary"]["active_watchlist_players"]==0
            assert client.get(f"/research/cycles/{cycle['id']}").status_code==200
            assert client.get("/research/cycles/latest").status_code==200
    finally:
        database.engine.dispose();Path(url.removeprefix("sqlite:///")) .unlink(missing_ok=True)
