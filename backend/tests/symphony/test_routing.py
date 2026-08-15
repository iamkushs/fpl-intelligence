import asyncio
from pathlib import Path

import pytest

from tools.symphony_runner.app_server import CodexAppServer
from tools.symphony_runner.config import RunnerConfig
from tools.symphony_runner.models import ConfigurationError, Issue, RunRecord
from tools.symphony_runner.routing import CatalogModel, ModelCatalog, ModelRouter

POLICY = {"defaultRoute": "5.5", "routes": {
    "luna": {"preferredEffort": "medium", "productiveFailureBudget": 1},
    "5.5": {"preferredEffort": "medium", "productiveFailureBudget": 2},
    "terra": {"preferredEffort": "high", "productiveFailureBudget": 2},
    "sol": {"preferredEffort": "high", "productiveFailureBudget": 0}},
    "escalationSequence": ["luna", "5.5", "terra", "sol"]}


def model(name, efforts=("low", "medium", "high"), hidden=False):
    return CatalogModel(name, name, name.replace("-", " "), "", efforts, hidden)


def router(*values): return ModelRouter(ModelCatalog(list(values) or [model("gpt-5.5")]), POLICY)
def issue(labels=()): return Issue(1, "x", "", tuple(labels), "open", "url")


@pytest.mark.parametrize(("labels", "route"), [((), "5.5"), (("model-5.5",), "5.5"),
    (("model-luna",), "luna"), (("model-terra",), "terra"), (("model-sol",), "sol")])
def test_label_routes(labels, route): assert ModelRouter.requested_route(issue(labels))[0] == route


def test_conflicting_labels_do_not_guess():
    with pytest.raises(ConfigurationError, match="conflicting"): ModelRouter.requested_route(issue(("model-sol", "model-luna")))


def test_resolution_preserves_actual_id_and_effort_order():
    result = router(model("account-gpt-5.6-terra", ("low", "medium", "xhigh"))).resolve("terra")
    assert result.model.id == "account-gpt-5.6-terra" and result.effort == "medium" and result.effort_fallback


def test_unavailable_and_ambiguous_are_safe():
    with pytest.raises(ConfigurationError, match="unavailable.*available models"): router(model("other")).resolve("sol")
    with pytest.raises(ConfigurationError, match="ambiguous"): router(model("gpt-5.6-sol"), model("preview-sol")).resolve("sol")


def test_human_label_change_resets_escalation_without_downgrading_requested_route():
    record = RunRecord(1, "w", "b", requested_model_route="luna", escalation_level=2, productive_failure_count=1)
    route, reason = router().reconcile(issue(("model-terra",)), record)
    assert route == "terra" and reason == "human label change" and record.escalation_level == 0


def test_bounded_productive_escalation_and_restart_state():
    record = RunRecord(1, "w", "b", requested_model_route="luna")
    r = router()
    assert r.record_productive_failure(record) and record.escalation_level == 1
    assert not r.record_productive_failure(record)
    assert r.record_productive_failure(record) and record.escalation_level == 2
    assert not r.record_productive_failure(record) and r.record_productive_failure(record)
    assert record.escalation_level == 3 and not r.record_productive_failure(record)
    assert record.previous_routes == ["luna", "5.5", "terra"]


def test_malformed_policy_cannot_make_sol_default():
    bad = {**POLICY, "defaultRoute": "sol"}
    with pytest.raises(ConfigurationError, match="Sol cannot"): ModelRouter(ModelCatalog([model("gpt-5.5")]), bad)


@pytest.mark.parametrize("mode", ["normal", "paginate"])
def test_catalog_retrieval_is_process_lifetime_cached(tmp_path, mode):
    fake = Path(__file__).with_name("fake_app_server.py")
    config = RunnerConfig("o/r", "", codex_command=(__import__('sys').executable, str(fake), mode))
    async def run():
        client = CodexAppServer(config, tmp_path, [], lambda *_: None)
        try:
            first = await client.model_catalog(); second = await client.model_catalog()
            return first, second
        finally: await client.close()
    first, second = asyncio.run(run())
    assert first is second and {m.id for m in first.models} >= {"gpt-5.5", "gpt-5.6-luna"}
