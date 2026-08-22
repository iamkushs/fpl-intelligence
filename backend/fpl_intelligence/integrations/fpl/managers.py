"""Public manager-entry endpoints, using the existing official HTTP boundary."""
from collections.abc import Mapping
from typing import Any, Protocol

from fpl_intelligence.integrations.fpl.adapter import OfficialFPLAdapter, _as_int, _required
from fpl_intelligence.integrations.fpl.errors import OfficialFPLSchemaError


class FPLManagerProvider(Protocol):
    def get_entry(self, entry_id: int) -> Mapping[str, Any]: ...
    def get_gameweek_picks(self, entry_id: int, gameweek: int) -> Mapping[str, Any]: ...
    def get_entry_history(self, entry_id: int) -> Mapping[str, Any]: ...


class OfficialFPLManagerProvider:
    def __init__(self, adapter: OfficialFPLAdapter): self.adapter = adapter
    def get_entry(self, entry_id: int) -> Mapping[str, Any]: return self._object(self.adapter._get_json(f"entry/{entry_id}/"), "entry")
    def get_gameweek_picks(self, entry_id: int, gameweek: int) -> Mapping[str, Any]: return self._object(self.adapter._get_json(f"entry/{entry_id}/event/{gameweek}/picks/"), "picks")
    def get_entry_history(self, entry_id: int) -> Mapping[str, Any]: return self._object(self.adapter._get_json(f"entry/{entry_id}/history/"), "history")
    @staticmethod
    def _object(payload: Any, context: str) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping): raise OfficialFPLSchemaError(f"Official FPL {context} response must be an object")
        return payload


def normalize_picks(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    picks = payload.get("picks")
    if not isinstance(picks, list) or len(picks) != 15: raise OfficialFPLSchemaError("Official FPL picks response must contain exactly 15 picks")
    normalized = []
    for item in picks:
        if not isinstance(item, Mapping): raise OfficialFPLSchemaError("Official FPL pick must be an object")
        normalized.append({"player_id": _as_int(_required(item, "element", context="pick"), field="element", context="pick"), "position": _as_int(_required(item, "position", context="pick"), field="position", context="pick"), "multiplier": _as_int(_required(item, "multiplier", context="pick"), field="multiplier", context="pick"), "is_captain": bool(item.get("is_captain", False)), "is_vice_captain": bool(item.get("is_vice_captain", False)), "purchase_price": _as_int(item["purchase_price"], field="purchase_price", context="pick") if item.get("purchase_price") is not None else None, "selling_price": _as_int(item["selling_price"], field="selling_price", context="pick") if item.get("selling_price") is not None else None})
    if {item["position"] for item in normalized} != set(range(1, 16)): raise OfficialFPLSchemaError("Official FPL picks must have positions 1 through 15")
    return normalized
