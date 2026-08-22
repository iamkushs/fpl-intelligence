"""Deterministic Match Center persistence and read model."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.models import (FPLManagerPair, FPLManagerPairMember, FPLManagerGameweekSnapshot, FPLMatchCenterSnapshot, FPLMatchCenterFixtureState, FPLMatchCenterPlayerState, FPLMatchCenterManagerState, Player)


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
        fields=("minutes","goals_scored","assists","clean_sheets","goals_conceded","bonus","bps","expected_goals","expected_assists","expected_goal_involvements","expected_goals_conceded")
        points={}
        for item in live:
            values={key:getattr(item,key) for key in fields}; points[item.player_id]=item.total_points or 0
            session.add(FPLMatchCenterPlayerState(snapshot_id=snapshot.id,player_id=item.player_id,total_points=item.total_points or 0,raw_stats=item.model_dump(exclude={"player_id"}),**values))
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
                    ps=points.get(p.player_id); return {"player":{"id":p.player_id,"display_name":f"Player {p.player_id}","href":f"/players/{p.player_id}"},"squad_position":p.squad_position,"starter":p.squad_position<=11,"captain":p.is_captain,"vice_captain":p.is_vice_captain,"multiplier":p.multiplier,"live_points":ps.total_points if ps else 0,"minutes":ps.minutes if ps else None,"fixture_state":"live_fixture" if any(f.started and not f.finished for f in snap.fixtures) else "fixture_finished" if snap.fixtures and all(f.finished for f in snap.fixtures) else "yet_to_play"}
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
            cap=next((p for p in m["starting_xi"]+m["bench"] if p["captain"]),None); vice=next((p for p in m["starting_xi"]+m["bench"] if p["vice_captain"]),None); extra=(cap["live_points"]*max(cap["multiplier"]-1,0)) if cap else 0; rows.append({"manager_id":m["id"],"side":m["side"],"captain":cap,"vice_captain":vice,"captain_extra_contribution":extra})
        ours=sum(x["captain_extra_contribution"] for x in rows if x["side"]=="ours"); opp=sum(x["captain_extra_contribution"] for x in rows if x["side"]=="opponent"); return {"managers":rows,"our_captain_contribution":ours,"opponent_captain_contribution":opp,"captaincy_swing":ours-opp}
    @staticmethod
    def calculate_autosub_watch(managers,snapshot):
        return [{"manager_id":m["id"],"status":"confirmed" if next((x for x in snapshot.managers if x.manager_id==m["id"]),None).automatic_subs else "pending","automatic_subs":next((x for x in snapshot.managers if x.manager_id==m["id"]),None).automatic_subs or []} for m in managers]
    @staticmethod
    def _pairs(session): return {p.side:p for p in session.scalars(select(FPLManagerPair).options(selectinload(FPLManagerPair.members).selectinload(FPLManagerPairMember.manager))).all()}
