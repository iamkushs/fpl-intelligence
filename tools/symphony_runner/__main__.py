from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

from .app_server import CodexAppServer
from .github import GitHubClient
from .orchestrator import Orchestrator
from .state import RunnerLock, StateStore
from .workflow import load_workflow
from .workspace import WorkspaceManager


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fpl-symphony", description="Windows-native Symphony runner for FPL Intelligence")
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("run", "validate", "models"):
        command = sub.add_parser(name); command.add_argument("workflow", nargs="?", default="WORKFLOW.md")
    sub.add_parser("status")
    smoke = sub.add_parser("smoke-test"); smoke.add_argument("workflow", nargs="?", default="WORKFLOW.md"); smoke.add_argument("--live-codex", action="store_true")
    cleanup = sub.add_parser("cleanup"); cleanup.add_argument("issue", type=int); cleanup.add_argument("--execute", action="store_true"); cleanup.add_argument("--workflow", default="WORKFLOW.md")
    return root


async def live_codex(config: object) -> None:
    from .config import RunnerConfig
    assert isinstance(config, RunnerConfig); assert config.paths
    safe = config.paths.root / "protocol-smoke"; safe.mkdir(parents=True, exist_ok=True)
    client = CodexAppServer(config, safe, [], lambda *_: None)
    try:
        result = await client.run_turn("Reply with exactly: protocol-ok")
        print(json.dumps({"thread_id": result.thread_id, "turn_id": result.turn_id, "status": result.status}))
    finally: await client.close()


async def print_models(config: object) -> None:
    from .config import RunnerConfig
    from .routing import ModelRouter, ROUTES
    assert isinstance(config, RunnerConfig); assert config.paths
    client = CodexAppServer(config, config.paths.root, [], lambda *_: None)
    try:
        catalog = await client.model_catalog(); router = ModelRouter.load(catalog, config.model_policy_path)
        print(f"{'Logical':<12}{'Resolved model':<36}Effort / supported")
        for route in ROUTES:
            try:
                result = router.resolve(route)
                print(f"{route:<12}{result.model.id:<36}{result.effort} / {', '.join(result.model.efforts)}")
            except Exception as exc: print(f"{route:<12}{'UNAVAILABLE':<36}{exc}")
    finally: await client.close()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "status":
        from .config import default_data_root
        store = StateStore(default_data_root() / "state.json"); store.load()
        compact = {}
        for key, record in store.records.items():
            incident = next((v for v in record.incidents if v.get("incident_id") == record.current_incident_id), {})
            compact[str(key)] = {"phase": record.phase, "status": record.status,
                "coder": {"route": record.requested_model_route, "model": record.resolved_model_id,
                          "effort": record.reasoning_effort},
                "reviewer": {"route": record.reviewer_route, "model": record.reviewer_model_id,
                             "effort": record.reviewer_effort, "verdict": record.reviewer_verdict},
                "incident": {"id": record.current_incident_id, "class": incident.get("failure_class"),
                             "repair_attempt": incident.get("repair_attempts", 0)},
                "productive_failure_count": record.productive_failure_count,
                "escalation": record.escalation_level, "thread_rotations": record.thread_rotations,
                "parked_for_infrastructure_repair": record.parked_for_maintenance}
        print(json.dumps(compact, indent=2)); return 0
    config = load_workflow(Path(getattr(args, "workflow", "WORKFLOW.md")))
    assert config.paths
    if args.command == "models":
        asyncio.run(print_models(config)); return 0
    if args.command in {"validate", "smoke-test"}:
        version = CodexAppServer.validate_schema()
        print(f"Workflow valid for {config.repository}; {version}; workspace={config.paths.workspaces}")
        if args.command == "smoke-test" and args.live_codex: asyncio.run(live_codex(config))
        return 0
    if args.command == "cleanup":
        with RunnerLock(config.paths.lock_file):
            github = GitHubClient(config.repository, config.tracker_token); issue = github.get_issue(args.issue)
            manager = WorkspaceManager(config.paths.workspaces, config.repository)
            allowed = issue.state == "closed" or "symphony-review" in issue.labels or "symphony-blocked" in issue.labels
            changed = manager.cleanup(issue, dry_run=not args.execute, allowed=allowed)
            print(f"{'Would remove' if not args.execute else 'Removed' if changed else 'Not eligible'} {manager.path_for(issue)}")
        return 0
    runner = Orchestrator(config)
    with RunnerLock(config.paths.lock_file):
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try: loop.add_signal_handler(sig, runner.stop)
            except NotImplementedError: signal.signal(sig, lambda *_: runner.stop())
        try: loop.run_until_complete(runner.run())
        except KeyboardInterrupt: runner.stop(); loop.run_until_complete(asyncio.sleep(0))
        finally: loop.close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
