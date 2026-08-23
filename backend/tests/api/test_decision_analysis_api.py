import json
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fpl_intelligence.app import create_app
from fpl_intelligence.models import (
    FPLManager,
    FPLManagerGameweekPick,
    FPLManagerGameweekSnapshot,
    FPLManagerPair,
    FPLManagerPairMember,
    Player,
)


class FakeCodex:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def execute(self, *, prompt):
        self.calls += 1
        return SimpleNamespace(final_text=self.payload)


def _client(database, payload, entry_id=10):
    session = database.session_factory()
    manager = FPLManager(entry_id=entry_id)
    session.add(manager)
    session.flush()
    pair = FPLManagerPair(name="Ours", side="ours")
    session.add(pair)
    session.flush()
    session.add(FPLManagerPairMember(pair_id=pair.id, manager_id=manager.id, slot=1))
    opponent = FPLManager(entry_id=entry_id + 100)
    opponent_pair = FPLManagerPair(name="Opponents", side="opponent")
    session.add_all([opponent, opponent_pair])
    session.flush()
    session.add(FPLManagerPairMember(pair_id=opponent_pair.id, manager_id=opponent.id, slot=1))
    snapshot = FPLManagerGameweekSnapshot(
        manager_id=manager.id,
        gameweek=2,
        bank=10,
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(snapshot)
    positions = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    session.add_all(Player(id=index, display_name=f"Player {index}", position=position, club_id=index)
                    for index, position in enumerate(positions, 1))
    session.flush()
    session.add_all(FPLManagerGameweekPick(snapshot_id=snapshot.id, player_id=index,
                    squad_position=index, multiplier=1, is_captain=False,
                    is_vice_captain=False, selling_price=50) for index in range(1, 16))
    session.commit()
    catalog = [SimpleNamespace(id=index, display_name=f"Player {index}", position=position,
               club_id=index, club_name=None, price=5.0) for index, position in enumerate(positions, 1)]
    gateway = FakeCodex(payload)
    app = create_app(database=database, codex_service=gateway)
    app.state.fpl_snapshot_service = SimpleNamespace(get_snapshot=lambda: SimpleNamespace(players=catalog))
    return TestClient(app), gateway, manager.id


def test_decision_analysis_endpoints_are_explicit_and_safe(database):
    payload = json.dumps({"outcome": "unresolved", "recommended_option_id": None,
        "confidence": "low", "executive_summary": "Research is incomplete.",
        "key_tradeoffs": [], "key_risks": [], "contradictions": [],
        "missing_information": ["Research"], "what_could_change_decision": [],
        "option_analyses": []})
    client, gateway, manager_id = _client(database, payload)
    created = client.post("/fpl/decisions/sessions", json={"manager_id": manager_id, "gameweek": 2})
    assert created.status_code == 201
    session_id = created.json()["id"]
    assert client.post(f"/fpl/decisions/sessions/{session_id}/hold").status_code == 201

    analysis = client.post(f"/fpl/decisions/sessions/{session_id}/analyze")
    assert analysis.status_code == 200
    assert analysis.json()["latest_analysis"]["outcome"] == "unresolved"
    calls_after_post = gateway.calls
    assert client.get(f"/fpl/decisions/sessions/{session_id}/analysis").status_code == 200
    assert len(client.get(f"/fpl/decisions/sessions/{session_id}/analysis/history").json()) == 1
    assert gateway.calls == calls_after_post
    queued = client.post(f"/fpl/decisions/sessions/{session_id}/research-queue", json={"player_ids": [1]})
    assert queued.status_code == 200
    assert any(item["player_id"] == 1 for item in client.get("/fpl/research-queue").json())

    gateway.payload = "not-json"
    response = client.post(f"/fpl/decisions/sessions/{session_id}/analyze")
    assert response.status_code == 422
    assert response.json()["detail"] == "decision_analysis_model_failed"
