from datetime import datetime, timezone
from uuid import uuid4
from fastapi.testclient import TestClient
from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.db.base import Base
from fpl_intelligence.db.session import Database
from fpl_intelligence.models import Player, ResearchThread, ResearchThreadType

def test_create_and_get_deep_run(tmp_path):
    settings=Settings(database_url=f"sqlite:///{tmp_path / (uuid4().hex + '.db')}"); database=Database(settings); Base.metadata.create_all(database.engine)
    app=create_app(settings,database=database,codex_service=CodexService(client=object(),settings=settings))
    with database.session_factory() as session:
        session.add(Player(id=1)); thread=ResearchThread(title="Player",thread_type=ResearchThreadType.PLAYER);session.add(thread);session.commit();thread_id=thread.id
    client=TestClient(app); response=client.post(f"/research/threads/{thread_id}/deep-runs",json={"player_id":1,"research_cutoff":datetime.now(timezone.utc).isoformat()}); assert response.status_code==201
    assert client.get(f"/research/deep-runs/{response.json()['id']}").status_code==200
    database.engine.dispose()
