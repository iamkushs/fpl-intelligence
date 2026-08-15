import asyncio
import time
from pathlib import Path

import pytest

from tools.symphony_runner.config import RunnerConfig
from tools.symphony_runner.models import Issue, RunRecord, RunnerPaths
from tools.symphony_runner.orchestrator import Orchestrator
from tools.symphony_runner.state import RunnerLock, StateStore


class GitHub:
    def __init__(self,issues): self.issues={i.number:i for i in issues}
    def list_candidates(self,_): return list(self.issues.values())
    def get_issue(self,n): return self.issues[n]


class Logger:
    def event(self,*_,**__): pass


def cfg(tmp_path,concurrency=2):
    paths=RunnerPaths(tmp_path,tmp_path/"spaces",tmp_path/"logs",tmp_path/"state.json",tmp_path/"lock")
    return RunnerConfig("o/r","",max_concurrent_agents=concurrency,paths=paths)


def issue(n,labels=("symphony",),state="open"): return Issue(n,f"I{n}","",labels,state,f"u{n}")


def test_eligibility_review_blocked_closed_and_unlabelled(tmp_path):
    runner=Orchestrator(cfg(tmp_path),github=GitHub([]),logger=Logger())
    assert runner.eligible(issue(1))
    assert not runner.eligible(issue(2,()))
    assert not runner.eligible(issue(3,("symphony","symphony-review")))
    assert not runner.eligible(issue(4,("symphony","symphony-blocked")))
    assert not runner.eligible(issue(5,state="closed"))


def test_cycle_respects_concurrency_and_never_duplicates(tmp_path):
    async def scenario():
        runner=Orchestrator(cfg(tmp_path,2),github=GitHub([issue(1),issue(2),issue(3)]),logger=Logger())
        gate=asyncio.Event(); starts=[]
        async def execute(value): starts.append(value.number); await gate.wait()
        runner._execute_issue=execute
        await runner.cycle(); await asyncio.sleep(0)
        assert sorted(starts)==[1,2] and len(runner.running)==2
        await runner.cycle(); await asyncio.sleep(0); assert starts==[1,2]
        gate.set(); await asyncio.gather(*runner.running.values())
    asyncio.run(scenario())


def test_retry_timestamp_defers_dispatch(tmp_path):
    async def scenario():
        runner=Orchestrator(cfg(tmp_path),github=GitHub([issue(1)]),logger=Logger())
        runner.store.records[1]=RunRecord(1,"w","b",retry_at=time.time()+100,status="retrying")
        await runner.cycle(); assert not runner.running
    asyncio.run(scenario())


def test_state_survives_restart_and_lock_rejects_second(tmp_path):
    store=StateStore(tmp_path/"state.json"); store.records[1]=RunRecord(1,"w","b",thread_id="t",attempt=2); store.save()
    loaded=StateStore(tmp_path/"state.json"); assert loaded.load()[1].thread_id=="t"
    first=RunnerLock(tmp_path/"lock"); second=RunnerLock(tmp_path/"lock"); first.acquire()
    try:
        with pytest.raises(RuntimeError): second.acquire()
    finally: first.release()


def test_losing_label_cancels_active_worker(tmp_path):
    async def scenario():
        gh=GitHub([issue(1)]); runner=Orchestrator(cfg(tmp_path),github=gh,logger=Logger())
        runner.running[1]=asyncio.create_task(asyncio.sleep(100))
        gh.issues[1]=issue(1,())
        await runner.cycle(); await asyncio.sleep(0)
        assert runner.running[1].cancelled() or runner.running[1].cancelling()
    asyncio.run(scenario())
