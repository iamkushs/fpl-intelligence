"""Deterministic durable squad and pair read model."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from fpl_intelligence.integrations.fpl.managers import FPLManagerProvider, normalize_picks
from fpl_intelligence.models import FPLManager, FPLManagerGameweekPick, FPLManagerGameweekSnapshot, FPLManagerPair, FPLManagerPairMember, Player

class PairConfigurationError(ValueError): pass

class PairSquadService:
    def __init__(self, provider: FPLManagerProvider | None = None): self.provider = provider
    def configure_pairs(self, session: Session, *, our_pair: dict, opponent_pair: dict):
        pairs = [("ours", our_pair), ("opponent", opponent_pair)]
        entries = []
        for side, data in pairs:
            ids = data.get("entry_ids", [])
            if not isinstance(ids, list) or len(ids) != 2 or len(set(ids)) != 2 or any(not isinstance(x, int) or x < 1 for x in ids): raise PairConfigurationError(f"{side} pair requires two distinct positive entry IDs")
            if not isinstance(data.get("name"), str) or not data["name"].strip(): raise PairConfigurationError(f"{side} pair name is required")
            entries.extend(ids)
        if len(set(entries)) != 4: raise PairConfigurationError("A manager cannot be on both configured pairs")
        for side, data in pairs:
            pair = session.scalar(select(FPLManagerPair).where(FPLManagerPair.side == side).options(selectinload(FPLManagerPair.members)))
            if pair is None: pair = FPLManagerPair(side=side, name=data["name"].strip()); session.add(pair); session.flush()
            pair.name = data["name"].strip()
            for member in list(pair.members): session.delete(member)
            session.flush()
            for slot, entry_id in enumerate(data["entry_ids"], 1):
                manager = session.scalar(select(FPLManager).where(FPLManager.entry_id == entry_id))
                if manager is None: manager = FPLManager(entry_id=entry_id); session.add(manager); session.flush()
                session.add(FPLManagerPairMember(pair_id=pair.id, manager_id=manager.id, slot=slot))
        session.commit(); return self.get_configuration(session)
    def get_configuration(self, session: Session):
        pairs = self._pairs(session)
        return {side: {"name": pair.name, "side": side, "entry_ids": [m.manager.entry_id for m in pair.members]} for side, pair in pairs.items()}
    def sync_manager(self, session: Session, entry_id: int, gameweek: int):
        if self.provider is None: raise RuntimeError("No FPL manager provider configured")
        entry = self.provider.get_entry(entry_id); picks_payload = self.provider.get_gameweek_picks(entry_id, gameweek); picks = normalize_picks(picks_payload)
        ids = [p["player_id"] for p in picks]; known = set(session.scalars(select(Player.id).where(Player.id.in_(ids))))
        if set(ids) != known: raise LookupError("Official squad contains players not available in the canonical player catalog")
        manager = session.scalar(select(FPLManager).where(FPLManager.entry_id == entry_id))
        if manager is None: manager = FPLManager(entry_id=entry_id); session.add(manager); session.flush()
        manager.manager_name = entry.get("player_first_name") and f"{entry.get('player_first_name')} {entry.get('player_last_name', '')}".strip(); manager.team_name = entry.get("name")
        history = self.provider.get_entry_history(entry_id); current = next((x for x in history.get("current", []) if isinstance(x, dict) and x.get("event") == gameweek), {})
        snapshot = session.scalar(select(FPLManagerGameweekSnapshot).where(FPLManagerGameweekSnapshot.manager_id == manager.id, FPLManagerGameweekSnapshot.gameweek == gameweek).options(selectinload(FPLManagerGameweekSnapshot.picks)))
        if snapshot is None: snapshot = FPLManagerGameweekSnapshot(manager_id=manager.id, gameweek=gameweek, fetched_at=datetime.now(timezone.utc)); session.add(snapshot); session.flush()
        snapshot.event_points=current.get("points"); snapshot.total_points=current.get("total_points"); snapshot.overall_rank=current.get("overall_rank"); snapshot.bank=picks_payload.get("entry_history", {}).get("bank"); snapshot.squad_value=picks_payload.get("entry_history", {}).get("value"); snapshot.active_chip=picks_payload.get("active_chip"); snapshot.fetched_at=datetime.now(timezone.utc)
        for pick in list(snapshot.picks): session.delete(pick)
        session.flush()
        for pick in picks: session.add(FPLManagerGameweekPick(snapshot_id=snapshot.id, player_id=pick["player_id"], squad_position=pick["position"], multiplier=pick["multiplier"], is_captain=pick["is_captain"], is_vice_captain=pick["is_vice_captain"], purchase_price=pick["purchase_price"], selling_price=pick["selling_price"]))
        manager.last_synced_at = snapshot.fetched_at; session.commit(); return {"entry_id": entry_id, "status": "synced"}
    def sync_all(self, session: Session, gameweek: int):
        entries = [m.manager.entry_id for p in self._pairs(session).values() for m in p.members]; results=[]
        for entry in entries:
            try: results.append(self.sync_manager(session, entry, gameweek))
            except Exception as exc: session.rollback(); results.append({"entry_id": entry, "status": "failed", "error": str(exc)})
        return results
    def get_pair_view(self, session: Session, gameweek: int | None = None):
        pairs=self._pairs(session)
        if gameweek is None: gameweek=session.scalar(select(FPLManagerGameweekSnapshot.gameweek).order_by(FPLManagerGameweekSnapshot.gameweek.desc()))
        if gameweek is None: return None
        payload={"gameweek":gameweek,"generated_at":datetime.now(timezone.utc),"configured_manager_count":sum(len(p.members) for p in pairs.values())}
        owners={}
        for side,pair in pairs.items():
            managers=[]
            for member in pair.members:
                snap=session.scalar(select(FPLManagerGameweekSnapshot).where(FPLManagerGameweekSnapshot.manager_id==member.manager_id,FPLManagerGameweekSnapshot.gameweek==gameweek).options(selectinload(FPLManagerGameweekSnapshot.picks)))
                def item(p): return {"player":{"id":p.player_id,"display_name":f"Player {p.player_id}"},"squad_position":p.squad_position,"multiplier":p.multiplier,"captain":p.is_captain,"vice_captain":p.is_vice_captain,"purchase_price":p.purchase_price,"selling_price":p.selling_price}
                picks=[] if snap is None else [item(p) for p in snap.picks]
                for p in picks: owners.setdefault(p["player"]["id"],[]).append({"side":side,"manager_id":member.manager_id,"slot":member.slot,"starting":p["squad_position"]<=11,"captain":p["captain"]})
                managers.append({"id":member.manager.id,"entry_id":member.manager.entry_id,"manager_name":member.manager.manager_name,"team_name":member.manager.team_name,"last_synced_at":member.manager.last_synced_at,"event_points":snap.event_points if snap else None,"total_points":snap.total_points if snap else None,"overall_rank":snap.overall_rank if snap else None,"bank":snap.bank if snap else None,"squad_value":snap.squad_value if snap else None,"active_chip":snap.active_chip if snap else None,"starting_xi":[p for p in picks if p["squad_position"]<=11],"bench":[p for p in picks if p["squad_position"]>11]})
            payload[f"{'our' if side == 'ours' else 'opponent'}_pair"]={"name":pair.name,"side":side,"managers":managers}
        exposure=[]
        for pid, items in sorted(owners.items()):
            ours=[x for x in items if x["side"]=="ours"]; opponents=[x for x in items if x["side"]=="opponent"]; total=len(items); count=payload["configured_manager_count"]
            state="universal" if total==count else "ours_only" if ours and not opponents else "opponent_only" if opponents and not ours else "shared"
            exposure.append({"player":{"id":pid,"display_name":f"Player {pid}"},"our_owner_count":len(ours),"opponent_owner_count":len(opponents),"our_owners":ours,"opponent_owners":opponents,"our_starting_count":sum(x["starting"] for x in ours),"opponent_starting_count":sum(x["starting"] for x in opponents),"our_captain_count":sum(x["captain"] for x in ours),"opponent_captain_count":sum(x["captain"] for x in opponents),"exposure_state":state})
        pair_key=lambda side: f"{'our' if side == 'ours' else 'opponent'}_pair"
        payload["exposure"]=exposure; payload["overlap"]={side:self._overlap(payload[pair_key(side)]["managers"]) for side in ("ours","opponent")}; payload["captaincy"]={side:[{"entry_id":m["entry_id"],"captain":next((p for p in m["starting_xi"] if p["captain"]),None),"vice_captain":next((p for p in m["starting_xi"]+m["bench"] if p["vice_captain"]),None)} for m in payload[pair_key(side)]["managers"]] for side in ("ours","opponent")}; return payload
    def get_pair_history(self, session: Session, limit=8):
        gameweeks=list(session.scalars(select(FPLManagerGameweekSnapshot.gameweek).distinct().order_by(FPLManagerGameweekSnapshot.gameweek.desc()).limit(limit))); return [{"gameweek":gw,"view":self.get_pair_view(session,gw)} for gw in gameweeks]
    def _pairs(self, session): return {p.side:p for p in session.scalars(select(FPLManagerPair).options(selectinload(FPLManagerPair.members).selectinload(FPLManagerPairMember.manager))).all()}
    @staticmethod
    def _overlap(managers):
        selections=[{p["player"]["id"] for p in m["starting_xi"]+m["bench"]} for m in managers]; shared=set.intersection(*selections) if len(selections)==2 else set(); all_ids=set.union(*selections) if selections else set(); starting=[{p["player"]["id"] for p in m["starting_xi"]} for m in managers]; captains=[next((p["player"]["id"] for p in m["starting_xi"] if p["captain"]),None) for m in managers]; return {"shared_player_count":len(shared),"unique_player_count":len(all_ids-shared),"shared_player_ids":sorted(shared),"unique_player_ids":sorted(all_ids-shared),"starting_xi_overlap_count":len(set.intersection(*starting)) if len(starting)==2 else 0,"captain_same":len(captains)==2 and captains[0] is not None and captains[0]==captains[1]}
