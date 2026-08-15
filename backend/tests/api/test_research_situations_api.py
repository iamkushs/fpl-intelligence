from fastapi.testclient import TestClient
from sqlalchemy import select

from fpl_intelligence.app import create_app
from fpl_intelligence.codex.service import CodexService
from fpl_intelligence.config import Settings
from fpl_intelligence.integrations.fpl.schemas import FPLPlayer
from fpl_intelligence.models import (
    Player,
    PlayerResearchTrigger,
    ResearchSituation,
    ResearchThreadType,
    SituationHypothesis,
)
from fpl_intelligence.research.persistence import ResearchPersistenceService
from fpl_intelligence.watchlist.service import WatchlistService


class SnapshotService:
    def get_snapshot(self):
        players = [
            FPLPlayer(
                id=player_id,
                first_name="Player",
                second_name=str(player_id),
                display_name=f"P{player_id}",
                club_id=player_id,
                club_name=f"Club {player_id}",
                club_short_name=f"C{player_id}",
                position="MID",
                price=7.0,
                availability_status="a",
            )
            for player_id in (1, 2, 3)
        ]
        return type("Snapshot", (), {"players": players, "current_gameweek": None})()


def app_for(database):
    return create_app(
        Settings(database_url="sqlite:///unused.db"),
        database=database,
        codex_service=CodexService(client=object()),
        fpl_snapshot_service=SnapshotService(),
    )


def situation_payload(**overrides):
    payload = {
        "title": "Arsenal right-side role",
        "club_id": 1,
        "context": "Minutes and touchline role are uncertain.",
        "fpl_relevance": "Affects expected starts and attacking involvement.",
        "player_ids": [1],
    }
    payload.update(overrides)
    return payload


def test_create_single_and_multi_player_situations_and_reject_unknown_player(database):
    with database.session_factory() as session:
        session.add_all([Player(id=1), Player(id=2)])
        session.commit()

    with TestClient(app_for(database)) as client:
        single = client.post("/research/situations", json=situation_payload())
        assert single.status_code == 201, single.text
        assert single.json()["players"] == [{"id": 1}]
        assert single.json()["status"] == "open"

        multi = client.post(
            "/research/situations",
            json=situation_payload(title="Penalty hierarchy", player_ids=[1, 2]),
        )
        assert multi.status_code == 201, multi.text
        assert [item["id"] for item in multi.json()["players"]] == [1, 2]

        unknown = client.post("/research/situations", json=situation_payload(player_ids=[999]))
        assert unknown.status_code == 404

    with database.session_factory() as session:
        assert session.query(ResearchSituation).count() == 2
        assert session.query(Player).count() == 2


def test_one_player_can_be_in_multiple_situations_and_duplicate_associations_are_avoided(database):
    with database.session_factory() as session:
        session.add_all([Player(id=1), Player(id=2)])
        session.commit()

    with TestClient(app_for(database)) as client:
        first = client.post("/research/situations", json=situation_payload(player_ids=[1, 1])).json()
        second = client.post(
            "/research/situations",
            json=situation_payload(title="Set-piece role", context="Corners are uncertain.", player_ids=[1, 2]),
        ).json()
        attached = client.post(f"/research/situations/{first['id']}/players", json={"player_ids": [1, 2, 2]})
        assert attached.status_code == 200, attached.text
        assert [item["id"] for item in attached.json()["players"]] == [1, 2]

        listed = client.get("/fpl/players/1/situations")
        assert listed.status_code == 200, listed.text
        assert {item["situation_id"] for item in listed.json()} == {first["id"], second["id"]}


def test_hypotheses_persist_and_status_updates(database):
    with database.session_factory() as session:
        session.add(Player(id=1))
        session.commit()

    with TestClient(app_for(database)) as client:
        situation = client.post("/research/situations", json=situation_payload()).json()
        hypothesis = client.post(
            f"/research/situations/{situation['id']}/hypotheses",
            json={"statement": "Player 1 is first choice.", "active": True},
        )
        assert hypothesis.status_code == 201, hypothesis.text
        updated = client.patch(f"/research/situations/{situation['id']}", json={"status": "leaning"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "leaning"
        loaded = client.get(f"/research/situations/{situation['id']}").json()
        assert loaded["hypotheses"][0]["statement"] == "Player 1 is first choice."

    with database.session_factory() as session:
        assert session.query(SituationHypothesis).count() == 1


def test_trigger_exists_without_situation_and_later_attaches_to_queue_context(database):
    with database.session_factory() as session:
        session.add_all([Player(id=1), Player(id=2), Player(id=3)])
        session.commit()

    with TestClient(app_for(database)) as client:
        bare = client.post("/fpl/players/3/trigger-research", json={"reason": "No context yet"})
        assert bare.status_code == 200, bare.text
        assert bare.json()["situation_id"] is None
        plain_queue = {item["player_id"]: item for item in client.get("/research/queue").json()}
        assert plain_queue[3]["situation_id"] is None

        situation = client.post(
            "/research/situations",
            json=situation_payload(title="Shared role", player_ids=[1, 2]),
        ).json()
        trigger = client.post("/fpl/players/1/trigger-research", json={"reason": "Review shared role"}).json()
        attach = client.post(
            f"/research/situations/{situation['id']}/triggers",
            json={"trigger_id": trigger["id"]},
        )
        assert attach.status_code == 200, attach.text

        queue = {item["player_id"]: item for item in client.get("/research/queue").json()}
        assert queue[1]["situation_id"] == situation["id"]
        assert queue[1]["situation_title"] == "Shared role"
        assert queue[1]["other_involved_players"] == [
            {"player_id": 2, "player_name": "P2", "club": "Club 2", "position": "MID"}
        ]
        assert queue[3]["situation_id"] is None

    with database.session_factory() as session:
        row = session.scalar(select(PlayerResearchTrigger).where(PlayerResearchTrigger.id == trigger["id"]))
        assert row.situation_id == situation["id"]


def test_research_thread_attaches_to_situation(database):
    persistence = ResearchPersistenceService()
    with database.session_factory() as session:
        session.add(Player(id=1))
        session.commit()
        thread = persistence.create_thread(session, title="Player research", thread_type=ResearchThreadType.PLAYER)

    with TestClient(app_for(database)) as client:
        situation = client.post("/research/situations", json=situation_payload()).json()
        attached = client.post(
            f"/research/situations/{situation['id']}/threads",
            json={"thread_id": thread.id},
        )
        assert attached.status_code == 200, attached.text
        assert attached.json() == {"thread_id": thread.id, "situation_id": situation["id"]}

    with database.session_factory() as session:
        assert persistence.repository.get_thread(session, thread.id).situation_id == situation["id"]


def test_player_details_expose_situation_context_and_survives_watchlist_removal(database):
    with database.session_factory() as session:
        session.add_all([Player(id=1), Player(id=2)])
        session.commit()
        WatchlistService().add(session, 1)

    with TestClient(app_for(database)) as client:
        situation = client.post(
            "/research/situations",
            json=situation_payload(title="Penalty hierarchy", player_ids=[1, 2]),
        ).json()
        client.post(
            f"/research/situations/{situation['id']}/hypotheses",
            json={"statement": "Player 1 remains first choice."},
        )

        before = client.get("/fpl/players/1")
        assert before.status_code == 200, before.text
        context = before.json()["current_research_context"]
        assert context[0]["situation_id"] == situation["id"]
        assert context[0]["title"] == "Penalty hierarchy"
        assert [item["player_id"] for item in context[0]["involved_players"]] == [1, 2]
        assert context[0]["active_hypotheses"][0]["statement"] == "Player 1 remains first choice."

        removed = client.request("DELETE", "/watchlist/1", json={"reason": "Reviewed"})
        assert removed.status_code == 200, removed.text
        after = client.get("/fpl/players/1")
        assert after.status_code == 200, after.text
        assert after.json()["watchlist"]["active"] is False
        assert after.json()["current_research_context"][0]["situation_id"] == situation["id"]
