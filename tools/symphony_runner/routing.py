from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ConfigurationError, Issue, RunRecord

ROUTES = ("5.5", "luna", "terra", "sol")
MODEL_LABELS = {f"model-{route}": route for route in ROUTES}


@dataclass(frozen=True, slots=True)
class CatalogModel:
    id: str
    model: str
    display_name: str
    description: str
    efforts: tuple[str, ...]
    hidden: bool = False

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "CatalogModel":
        efforts = tuple(str(v.get("reasoningEffort")) for v in value.get("supportedReasoningEfforts", []) if v.get("reasoningEffort"))
        return cls(str(value["id"]), str(value.get("model", "")), str(value.get("displayName", "")),
                   str(value.get("description", "")), efforts, bool(value.get("hidden", False)))


@dataclass(frozen=True, slots=True)
class RouteResolution:
    route: str
    model: CatalogModel
    effort: str
    effort_fallback: bool = False


class ModelCatalog:
    def __init__(self, models: list[CatalogModel]):
        self.models = tuple(models)

    def describe(self) -> str:
        return ", ".join(f"{m.id} ({m.display_name}; efforts={','.join(m.efforts) or 'none'})" for m in self.models) or "<empty>"


class ModelRouter:
    def __init__(self, catalog: ModelCatalog, policy: dict[str, Any]):
        self.catalog, self.policy = catalog, policy
        self._validate_policy()

    @classmethod
    def load(cls, catalog: ModelCatalog, path: Path) -> "ModelRouter":
        try: policy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise ConfigurationError(f"invalid model routing configuration: {exc}") from exc
        return cls(catalog, policy)

    def _validate_policy(self) -> None:
        if self.policy.get("defaultRoute") != "5.5":
            raise ConfigurationError("model routing defaultRoute must be 5.5; Sol cannot become the default")
        sequence = self.policy.get("escalationSequence")
        if sequence != ["luna", "5.5", "terra", "sol"]:
            raise ConfigurationError("model escalationSequence must be luna, 5.5, terra, sol")
        if set(self.policy.get("routes", {})) != set(ROUTES): raise ConfigurationError("model routing must configure exactly 5.5, luna, terra, sol")

    @staticmethod
    def requested_route(issue: Issue) -> tuple[str, str]:
        labels = [label for label in issue.labels if label in MODEL_LABELS]
        if len(labels) > 1: raise ConfigurationError(f"conflicting model labels: {', '.join(sorted(labels))}")
        return (MODEL_LABELS[labels[0]], "explicit label") if labels else ("5.5", "default")

    @staticmethod
    def _tokens(model: CatalogModel) -> set[str]:
        text = " ".join((model.id, model.model, model.display_name)).lower()
        return set(re.findall(r"[a-z]+|\d+(?:\.\d+)?", text))

    def _matches(self, route: str) -> list[CatalogModel]:
        matches = []
        for model in self.catalog.models:
            tokens = self._tokens(model)
            structured = {model.id.lower(), model.model.lower(), model.display_name.lower()}
            if route == "5.5": ok = "5.5" in tokens or any(v in structured for v in {"gpt-5.5", "gpt 5.5"})
            else: ok = route in tokens or any(v.endswith(f"-{route}") or v.endswith(f" {route}") for v in structured)
            if ok and not model.hidden: matches.append(model)
        return matches

    def resolve(self, route: str) -> RouteResolution:
        if route not in ROUTES: raise ConfigurationError(f"unknown logical model route: {route}")
        matches = self._matches(route)
        if len(matches) != 1:
            why = "unavailable" if not matches else f"ambiguous ({', '.join(m.id for m in matches)})"
            raise ConfigurationError(f"logical model route {route} is {why}; available models: {self.catalog.describe()}")
        model = matches[0]; preferred = str(self.policy["routes"][route]["preferredEffort"])
        if not model.efforts: raise ConfigurationError(f"model {model.id} advertises no reasoning efforts")
        if preferred in model.efforts: effort, fallback = preferred, False
        else:
            # The server's list is the authoritative low-to-high progression. Pick
            # the nearest advertised position to medium/high without using max/ultra.
            safe = [v for v in model.efforts if v not in {"max", "ultra"}]
            if not safe: raise ConfigurationError(f"model {model.id} only advertises disallowed max/ultra efforts")
            anchors = ["none", "minimal", "low", "medium", "high", "xhigh"]
            target = anchors.index(preferred) if preferred in anchors else len(anchors) // 2
            effort = min(safe, key=lambda v: abs((anchors.index(v) if v in anchors else model.efforts.index(v)) - target)); fallback = True
        return RouteResolution(route, model, effort, fallback)

    def reconcile(self, issue: Issue, record: RunRecord) -> tuple[str, str]:
        requested, reason = self.requested_route(issue)
        if record.requested_model_route != requested:
            changed = record.requested_model_route is not None
            record.requested_model_route = requested; record.previous_routes = []
            record.escalation_level = 0; record.productive_failure_count = 0
            record.routing_reason = "human label change" if changed else reason
        sequence = self.policy["escalationSequence"]
        start = sequence.index(requested)
        return sequence[min(start + record.escalation_level, len(sequence) - 1)], record.routing_reason or reason

    def record_productive_failure(self, record: RunRecord) -> bool:
        sequence = self.policy["escalationSequence"]
        current = sequence[min(sequence.index(record.requested_model_route or "5.5") + record.escalation_level, len(sequence)-1)]
        record.productive_failure_count += 1
        budget = int(self.policy["routes"][current]["productiveFailureBudget"])
        if current != "sol" and record.productive_failure_count >= budget:
            record.previous_routes.append(current); record.escalation_level += 1; record.productive_failure_count = 0
            record.routing_reason = f"productive failure budget exhausted for {current}"
            record.thread_id = None
            return True
        return False
