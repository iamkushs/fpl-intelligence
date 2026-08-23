import json
from types import SimpleNamespace
import pytest
from fpl_intelligence.decisions.selection import SelectionError, SelectionService, validate_selection
from fpl_intelligence.decisions.service import DecisionService
from test_decision_service import prepared, players

class FakeCodex:
    def __init__(self,payload): self.payload=payload
    def execute(self,*,prompt): return SimpleNamespace(final_text=json.dumps(self.payload))

def valid(): return {'outcome':'recommendation','recommended_starting_xi_player_ids':[1,3,4,5,6,8,9,10,11,12,13],'recommended_bench_player_ids_in_order':[2,7,14,15],'recommended_captain_player_id':8,'recommended_vice_player_id':9,'confidence':'medium','executive_summary':'Secure starters.','captaincy_reasoning':'Role.','lineup_reasoning':'Formation.','bench_reasoning':'GK last.','key_risks':[],'contradictions':[],'missing_information':[],'what_could_change_decision':[]}

def test_rules_validate_formation_complement_and_captaincy():
    p=players(); squad=list(range(1,16)); data=valid()
    assert not validate_selection(squad,p,data['recommended_starting_xi_player_ids'],data['recommended_bench_player_ids_in_order'],8,9)
    assert 'starting_xi_requires_at_least_3_def' in validate_selection(squad,p,[1,3,8,9,10,11,12,13,14,15,5],[2,4,6,7],8,9)
    assert 'captain_and_vice_must_differ' in validate_selection(squad,p,data['recommended_starting_xi_player_ids'],data['recommended_bench_player_ids_in_order'],8,8)

def test_analysis_freezes_context_and_never_alters_user_plan(database):
    db=database.session_factory(); manager,_=prepared(db)
    for player_id, player in players().items():
        if player_id <= 15: db.get(__import__('fpl_intelligence.models',fromlist=['Player']).Player,player_id).position=player.position
    item=DecisionService().create_or_reuse(db,manager_id=manager.id,gameweek=2); service=SelectionService()
    run=service.analyze(db,item.id,FakeCodex(valid())); assert len(run.reasoning['packet']['squad'])==15 and run.recommended_captain_player_id==8
    assert db.scalar(__import__('sqlalchemy').select(__import__('fpl_intelligence.models',fromlist=['GameweekSelection']).GameweekSelection)) is None
    saved=service.apply(db,item.id,run.id); assert saved.captain_player_id==8
    service.finalize(db,item.id)
    with pytest.raises(SelectionError): service.save(db,item.id,valid()['recommended_starting_xi_player_ids'],valid()['recommended_bench_player_ids_in_order'],8,9)

def test_invalid_model_and_research_required_are_safe(database):
    db=database.session_factory(); manager,_=prepared(db)
    for player_id, player in players().items():
        if player_id <= 15: db.get(__import__('fpl_intelligence.models',fromlist=['Player']).Player,player_id).position=player.position
    item=DecisionService().create_or_reuse(db,manager_id=manager.id,gameweek=2); service=SelectionService()
    waiting={'outcome':'research_required','recommended_starting_xi_player_ids':[],'recommended_bench_player_ids_in_order':[],'recommended_captain_player_id':None,'recommended_vice_player_id':None}
    assert service.analyze(db,item.id,FakeCodex(waiting)).outcome=='research_required'
    bad=valid(); bad['recommended_captain_player_id']=2
    with pytest.raises(SelectionError,match='invalid_selection_analysis_output'): service.analyze(db,item.id,FakeCodex(bad))
