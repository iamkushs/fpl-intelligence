import json
from types import SimpleNamespace

import pytest

from fpl_intelligence.decisions.analysis import DecisionAnalysisError, DecisionAnalysisService
from fpl_intelligence.decisions.service import DecisionService
from test_decision_service import prepared, players


class FakeCodex:
    def __init__(self, payload): self.payload=payload; self.prompts=[]
    def execute(self, *, prompt): self.prompts.append(prompt); return SimpleNamespace(final_text=json.dumps(self.payload))


def test_analysis_only_sends_legal_options_and_preserves_user_choice(database):
    db=database.session_factory(); manager,_=prepared(db); decisions=DecisionService(); item=decisions.create_or_reuse(db,manager_id=manager.id,gameweek=2)
    hold=decisions.add_hold(db,item.id,players()); legal=decisions.add_transfers(db,item.id,[{"outgoing_player_id":12,"incoming_player_id":16}],players()); invalid=decisions.add_transfers(db,item.id,[{"outgoing_player_id":99,"incoming_player_id":16}],players())
    candidate=DecisionAnalysisService().packet(db,item.id)["legal_options"][0]["id"]
    fake=FakeCodex({"outcome":"recommend_option","recommended_option_id":candidate,"confidence":"medium","executive_summary":"Hold is supported.","key_tradeoffs":[],"key_risks":[],"contradictions":[],"missing_information":[],"what_could_change_decision":[],"option_analyses":[{"option_id":legal.id,"summary":"Alternative"}]})
    run=DecisionAnalysisService().analyze(db,item.id,fake)
    assert run.recommended_option_id==candidate and item.selected_option_id is None and item.status=="draft"
    packet=json.loads(fake.prompts[0].split("\n",1)[1]); assert {x["id"] for x in packet["legal_options"]}=={hold.id,legal.id} and invalid.id not in fake.prompts[0]
    assert all(g["freshness_state"]=="missing" for g in packet["research_context"])


def test_invalid_and_malformed_model_output_fail_safely(database):
    db=database.session_factory(); manager,_=prepared(db); item=DecisionService().create_or_reuse(db,manager_id=manager.id,gameweek=2); hold=DecisionService().add_hold(db,item.id,players())
    with pytest.raises(DecisionAnalysisError,match="invalid_decision_analysis_output"):
        DecisionAnalysisService().analyze(db,item.id,FakeCodex({"outcome":"recommend_option","recommended_option_id":"not-legal"}))
    with pytest.raises(DecisionAnalysisError,match="decision_analysis_model_failed"):
        DecisionAnalysisService().analyze(db,item.id,FakeCodex("not-json"))
    assert hold.id and len(DecisionAnalysisService().history(db,item.id))==2


def test_explicit_queue_is_idempotent_and_finalized_sessions_cannot_analyze(database):
    db=database.session_factory(); manager,_=prepared(db); service=DecisionService(); item=service.create_or_reuse(db,manager_id=manager.id,gameweek=2); hold=service.add_hold(db,item.id,players())
    first=DecisionAnalysisService().queue_gaps(db,item.id,[1]); second=DecisionAnalysisService().queue_gaps(db,item.id,[1]); assert first[0].id==second[0].id
    service.select(db,item.id,hold.id); service.finalize(db,item.id)
    with pytest.raises(DecisionAnalysisError,match="decision_session_is_finalized"): DecisionAnalysisService().analyze(db,item.id,FakeCodex({}))
