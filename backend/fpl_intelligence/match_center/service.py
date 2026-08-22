"""Deterministic Match Center persistence and read model."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.models import (FPLManagerPair, FPLManagerPairMember, FPLManagerGameweekSnapshot, FPLMatchCenterSnapshot, FPLMatchCenterFixtureState, FPLMatchCenterPlayerState, FPLMatchCenterManagerState, Player, ResearchPlayerSynthesis)


class MatchCenterConfigurationError(ValueError): pass


class MatchCenterService:
    def __init__(self, live_provider, manager_provider): self.live_provider=live_provider; self.manager_provider=manager_provider
    def refresh(self, session: Session, gameweek: int):
        pairs=self._pairs(session)
        if set(pairs) != {"ours","opponent"} or any(len(x.members)!=2 for x in pairs.values()): raise MatchCenterConfigurationError("Configure both manager pairs before refreshing Match Center")
        now=datetime.now(timezone.utc)
        try:
            live=self.live_provider.get_event_live(gameweek) if hasattr(self.live_provider,"get_event_live") else self.live_provider.get_gameweek_live(gameweek)
            fixtures=self.live_provider.get_fixtures(gameweek)
        except Exception as exc:
            snapshot=session.scalar(select(FPLMatchCenterSnapshot).where(FPLMatchCenterSnapshot.gameweek==gameweek))
            if snapshot is None:
                snapshot=FPLMatchCenterSnapshot(gameweek=gameweek,status="unavailable",fetched_at=now,failure_reason=str(exc)); session.add(snapshot); session.commit()
            return self.get_match_center(session, gameweek)
        known=set(session.scalars(select(Player.id).where(Player.id.in_([x.player_id for x in live]))))
        if any(x.player_id not in known for x in live): raise LookupError("Official live data contains players not available in the canonical player catalog")
        snapshot=session.scalar(select(FPLMatchCenterSnapshot).where(FPLMatchCenterSnapshot.gameweek==gameweek).options(selectinload(FPLMatchCenterSnapshot.fixtures),selectinload(FPLMatchCenterSnapshot.players),selectinload(FPLMatchCenterSnapshot.managers)))
        if snapshot is None: snapshot=FPLMatchCenterSnapshot(gameweek=gameweek,status="available",fetched_at=now); session.add(snapshot); session.flush()
        snapshot.status="available"; snapshot.fetched_at=now; snapshot.failure_reason=None
        for row in list(snapshot.fixtures)+list(snapshot.players): session.delete(row)
        session.flush()
        for f in fixtures:
            if f.id is not None: session.add(FPLMatchCenterFixtureState(snapshot_id=snapshot.id,official_fixture_id=f.id,home_team_id=f.home_club_id,away_team_id=f.away_club_id,kickoff_time=f.kickoff,started=f.started,finished=f.finished,finished_provisional=f.finished_provisional,fixture_minutes=f.minutes,home_score=f.home_score,away_score=f.away_score))
        bootstrap=self.live_provider.get_bootstrap() if hasattr(self.live_provider,"get_bootstrap") else None
        player_context={x.id:x for x in bootstrap.players} if bootstrap is not None else {}
        fields=("minutes","goals_scored","assists","clean_sheets","goals_conceded","bonus","bps","expected_goals","expected_assists","expected_goal_involvements","expected_goals_conceded")
        points={}
        for item in live:
            values={key:getattr(item,key) for key in fields}; points[item.player_id]=item.total_points or 0
            context=player_context.get(item.player_id)
            session.add(FPLMatchCenterPlayerState(snapshot_id=snapshot.id,player_id=item.player_id,club_id=context.club_id if context else None,position=context.position if context else None,total_points=item.total_points or 0,raw_stats=item.model_dump(exclude={"player_id"}),**values))
        session.flush(); failures=[]
        for pair in pairs.values():
            for member in pair.members:
                squad=session.scalar(select(FPLManagerGameweekSnapshot).where(FPLManagerGameweekSnapshot.manager_id==member.manager_id,FPLManagerGameweekSnapshot.gameweek==gameweek).options(selectinload(FPLManagerGameweekSnapshot.picks)))
                if squad is None: failures.append(f"manager {member.manager.entry_id} has no synced squad"); continue
                row=session.scalar(select(FPLMatchCenterManagerState).where(FPLMatchCenterManagerState.snapshot_id==snapshot.id,FPLMatchCenterManagerState.manager_id==member.manager_id))
                try:
                    payload=self.manager_provider.get_gameweek_picks(member.manager.entry_id,gameweek)
                    history=payload.get("entry_history",{}) if isinstance(payload,dict) else {}
                    automatic_subs=payload.get("automatic_subs") if isinstance(payload,dict) and isinstance(payload.get("automatic_subs"),list) else None
                    event_points=history.get("points")
                except Exception as exc:
                    failures.append(f"manager {member.manager.entry_id}: {exc}"); payload={}; automatic_subs=None; event_points=None
                    if row is not None: continue
                provisional=sum((points.get(p.player_id,0)*p.multiplier) for p in squad.picks)
                if row is None: row=FPLMatchCenterManagerState(snapshot_id=snapshot.id,manager_id=member.manager_id,squad_snapshot_id=squad.id,fetched_at=now); session.add(row)
                row.squad_snapshot_id=squad.id; row.official_event_points=event_points; row.provisional_live_points=provisional; row.active_chip=payload.get("active_chip") if isinstance(payload,dict) else None; row.automatic_subs=automatic_subs; row.fetched_at=now
        if failures: snapshot.status="partial"; snapshot.failure_reason="; ".join(failures)
        session.commit(); session.expire_all(); return self.get_match_center(session,gameweek)

    def get_latest_match_center(self, session):
        gameweek=session.scalar(select(FPLMatchCenterSnapshot.gameweek).order_by(FPLMatchCenterSnapshot.gameweek.desc()))
        return self.get_match_center(session,gameweek) if gameweek is not None else None
    def get_manager_live_state(self, session, gameweek, manager_id):
        return session.scalar(select(FPLMatchCenterManagerState).join(FPLMatchCenterSnapshot).where(FPLMatchCenterSnapshot.gameweek==gameweek,FPLMatchCenterManagerState.manager_id==manager_id))
    def get_match_center(self, session, gameweek):
        if gameweek is None: return None
        snap=session.scalar(select(FPLMatchCenterSnapshot).where(FPLMatchCenterSnapshot.gameweek==gameweek).options(selectinload(FPLMatchCenterSnapshot.fixtures),selectinload(FPLMatchCenterSnapshot.players),selectinload(FPLMatchCenterSnapshot.managers).selectinload(FPLMatchCenterManagerState.squad_snapshot).selectinload(FPLManagerGameweekSnapshot.picks),selectinload(FPLMatchCenterSnapshot.managers).selectinload(FPLMatchCenterManagerState.manager)))
        if snap is None:return None
        pairs=self._pairs(session); points={x.player_id:x for x in snap.players}; uses={}; managers=[]
        for side,pair in pairs.items():
            for member in pair.members:
                state=next((x for x in snap.managers if x.manager_id==member.manager_id),None)
                if not state: continue
                picks=state.squad_snapshot.picks
                def pick(p):
                    ps=points.get(p.player_id); fixture=self._fixture_context(ps,snap.fixtures); synthesis=session.scalar(select(ResearchPlayerSynthesis).where(ResearchPlayerSynthesis.player_id==p.player_id).order_by(ResearchPlayerSynthesis.research_cutoff.desc()))
                    return {"player":{"id":p.player_id,"display_name":f"Player {p.player_id}","href":f"/players/{p.player_id}","intelligence":None if synthesis is None else {"id":synthesis.id,"overall_research_state":synthesis.overall_research_state,"research_cutoff":synthesis.research_cutoff}},"squad_position":p.squad_position,"starter":p.squad_position<=11,"captain":p.is_captain,"vice_captain":p.is_vice_captain,"multiplier":p.multiplier,"position":ps.position if ps else None,"live_points":ps.total_points if ps else 0,"minutes":ps.minutes if ps else None,"fixture":fixture,"fixture_state":fixture["state"] if fixture else "unresolved"}
                rows=[pick(p) for p in picks]
                for row in rows: uses.setdefault(row["player"]["id"],[]).append({"side":side,"manager_id":member.manager_id,"manager_slot":member.slot,"manager_name":member.manager.manager_name,"starter":row["starter"],"captain":row["captain"],"vice_captain":row["vice_captain"],"multiplier":row["multiplier"]})
                managers.append({"id":member.manager_id,"entry_id":member.manager.entry_id,"slot":member.slot,"side":side,"manager_name":member.manager.manager_name,"team_name":member.manager.team_name,"provisional_live_points":state.provisional_live_points,"official_event_points":state.official_event_points,"active_chip":state.active_chip,"starting_xi":[x for x in rows if x["starter"]],"bench":[x for x in rows if not x["starter"]],"fixture_progress":{"starters_live_fixture":sum(x["fixture_state"]=="live_fixture" for x in rows if x["starter"]),"starters_fixture_finished":sum(x["fixture_state"]=="fixture_finished" for x in rows if x["starter"]),"starters_yet_to_play":sum(x["fixture_state"]=="yet_to_play" for x in rows if x["starter"])}})
        swings=[]; groups={"ours_only":[],"opponent_only":[],"shared":[],"universal":[]}
        for pid, owners in uses.items():
            ours=[x for x in owners if x["side"]=="ours"]; opp=[x for x in owners if x["side"]=="opponent"]; live=points.get(pid).total_points if pid in points else 0; state="universal" if len(owners)==4 else "ours_only" if ours and not opp else "opponent_only" if opp and not ours else "shared"; item={"player":{"id":pid,"display_name":f"Player {pid}","href":f"/players/{pid}"},"live_points":live,"our_owner_count":len(ours),"opponent_owner_count":len(opp),"our_effective_multiplier_sum":sum(x["multiplier"] for x in ours),"opponent_effective_multiplier_sum":sum(x["multiplier"] for x in opp),"our_live_contribution":live*sum(x["multiplier"] for x in ours),"opponent_live_contribution":live*sum(x["multiplier"] for x in opp),"exposure_state":state,"manager_usage":owners}; item["net_pair_swing"]=item["our_live_contribution"]-item["opponent_live_contribution"]; swings.append(item); groups[state].append(item)
        swings.sort(key=lambda x:(-abs(x["net_pair_swing"]),x["player"]["id"]))
        our=[x for x in managers if x["side"]=="ours"]; opp=[x for x in managers if x["side"]=="opponent"]
        captaincy=self.calculate_captaincy(managers)
        return {"gameweek":gameweek,"snapshot_status":snap.status,"fetched_at":snap.fetched_at,"failure_reason":snap.failure_reason,"fixtures":[{"id":x.official_fixture_id,"home_team_id":x.home_team_id,"away_team_id":x.away_team_id,"kickoff":x.kickoff_time,"started":x.started,"finished":x.finished,"score":{"home":x.home_score,"away":x.away_score}} for x in sorted(snap.fixtures,key=lambda x:(x.kickoff_time is None,x.kickoff_time))],"scoreboard":{"our_pair":{"name":pairs.get("ours").name if pairs.get("ours") else "Our pair","managers":our,"total":sum(x["provisional_live_points"] or 0 for x in our)},"opponent_pair":{"name":pairs.get("opponent").name if pairs.get("opponent") else "Opponent pair","managers":opp,"total":sum(x["provisional_live_points"] or 0 for x in opp)}},"managers":sorted(managers,key=lambda x:(x["side"]!="ours",x["slot"])),"captaincy":captaincy,"exposure":groups,"player_swings":swings,"autosub_watch":self.calculate_autosub_watch(managers,snap)} | {"scoreboard": {"our_pair":{"name":pairs.get("ours").name if pairs.get("ours") else "Our pair","managers":our,"total":sum(x["provisional_live_points"] or 0 for x in our)},"opponent_pair":{"name":pairs.get("opponent").name if pairs.get("opponent") else "Opponent pair","managers":opp,"total":sum(x["provisional_live_points"] or 0 for x in opp)},"pair_live_swing":sum(x["provisional_live_points"] or 0 for x in our)-sum(x["provisional_live_points"] or 0 for x in opp)}}
    @staticmethod
    def calculate_captaincy(managers):
        rows=[]
        for m in managers:
            cap=next((p for p in m["starting_xi"]+m["bench"] if p["captain"]),None); vice=next((p for p in m["starting_xi"]+m["bench"] if p["vice_captain"]),None); effective=cap; status="none"
            if cap and cap["fixture_state"] in {"yet_to_play","live_fixture","unresolved"}: status="pending"
            elif cap and cap["fixture_state"]=="fixture_finished" and (cap["minutes"] or 0)==0:
                if vice and (vice["minutes"] or 0)>0: effective={**vice,"multiplier":cap["multiplier"]}; status="provisional"
                else: status="pending"
            extra=(effective["live_points"]*max(effective["multiplier"]-1,0)) if effective else 0; rows.append({"manager_id":m["id"],"side":m["side"],"captain":cap,"vice_captain":vice,"effective_captain":effective,"fallback_status":status,"captain_extra_contribution":extra})
        ours=sum(x["captain_extra_contribution"] for x in rows if x["side"]=="ours"); opp=sum(x["captain_extra_contribution"] for x in rows if x["side"]=="opponent"); return {"managers":rows,"our_captain_contribution":ours,"opponent_captain_contribution":opp,"captaincy_swing":ours-opp}
    def calculate_autosub_watch(self,managers,snapshot):
        result=[]
        for manager in managers:
            state=next(x for x in snapshot.managers if x.manager_id==manager["id"])
            if state.automatic_subs:
                result.append({"manager_id":manager["id"],"status":"confirmed","automatic_subs":state.automatic_subs,"items":[{"status":"confirmed","reason":"Official FPL automatic substitution"}]}); continue
            starters=sorted(manager["starting_xi"],key=lambda x:x["squad_position"]); bench=sorted(manager["bench"],key=lambda x:x["squad_position"]); items=[]
            pending=any(x["fixture_state"] in {"yet_to_play","live_fixture","unresolved"} and (x["minutes"] or 0)==0 for x in starters)
            effective=[x for x in starters if not (x["fixture_state"]=="fixture_finished" and (x["minutes"] or 0)==0)]
            for outgoing in starters:
                if not (outgoing["fixture_state"]=="fixture_finished" and (outgoing["minutes"] or 0)==0): continue
                candidates=[x for x in bench if x["position"]==outgoing["position"]] if outgoing["position"]=="GKP" else [x for x in bench if x["position"]!="GKP"]
                incoming=next((x for x in candidates if self._legal_replacement(effective,outgoing,x)),None)
                if incoming is None: items.append({"outgoing":outgoing["player"],"incoming":None,"status":"pending" if pending else "provisional","reason":"No eligible bench replacement in bench order"}); continue
                effective.remove(outgoing); effective.append(incoming); bench.remove(incoming)
                status="provisional" if incoming["fixture_state"]=="fixture_finished" else "pending"
                items.append({"outgoing":outgoing["player"],"incoming":incoming["player"],"status":status,"reason":"Bench order and formation rules"})
            result.append({"manager_id":manager["id"],"status":"pending" if pending and not items else ("provisional" if items else "pending"),"automatic_subs":[],"items":items})
        return result
    @staticmethod
    def _legal_replacement(effective,outgoing,incoming):
        trial=[x for x in effective if x is not outgoing]+[incoming]
        positions=[x["position"] for x in trial]
        return len(trial)<=11 and positions.count("GKP")==1 and positions.count("DEF")>=3 and positions.count("MID")>=2 and positions.count("FWD")>=1
    @staticmethod
    def _fixture_context(player_state,fixtures):
        if player_state is None or player_state.club_id is None:return None
        fixture=next((x for x in fixtures if player_state.club_id in (x.home_team_id,x.away_team_id)),None)
        if fixture is None:return None
        state="fixture_finished" if fixture.finished or fixture.finished_provisional else "live_fixture" if fixture.started else "yet_to_play"
        return {"official_fixture_id":fixture.official_fixture_id,"opponent_team_id":fixture.away_team_id if player_state.club_id==fixture.home_team_id else fixture.home_team_id,"home_away":"home" if player_state.club_id==fixture.home_team_id else "away","kickoff_time":fixture.kickoff_time,"score":{"home":fixture.home_score,"away":fixture.away_score},"state":state}
    @staticmethod
    def _pairs(session): return {p.side:p for p in session.scalars(select(FPLManagerPair).options(selectinload(FPLManagerPair.members).selectinload(FPLManagerPairMember.manager))).all()}
