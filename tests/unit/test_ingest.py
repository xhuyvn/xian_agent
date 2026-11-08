"""Unit tests for the stoppable, resumable ingest run (G-29)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from xn.cli.main import app
from xn.pipeline.ingest import IngestError, pid_alive, request_stop, run_ingest
from xn.pipeline.runner import STAGES
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

CONFIG = {"total_chapters": 120, "models": {"s1_extractor": {"serving_name": "s1m"}, "local_extractor": {"name": "loc"}}}


@pytest.fixture
def repo(tmp_path: Path):
    conn = connect(tmp_path / "xn.db")
    init_schema(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


class FakeStages:
    """Records calls; can fail a stage, raise, or ask for a stop after a given call."""

    def __init__(self, repo: Repository, *, fail: str | None = None, boom: str | None = None,
                 stop_after: tuple[str, int] | None = None) -> None:
        self.repo, self.fail, self.boom, self.stop_after = repo, fail, boom, stop_after
        self.calls: list[tuple[str, int, int | None]] = []

    def __call__(self, repo: Repository, *, stage: str, novel_id: str, config: dict[str, Any],
                 chapter_from: int = 1, chapter_to: int | None = None, **_: Any) -> Any:
        self.calls.append((stage, chapter_from, chapter_to))
        if stage == self.boom:
            raise RuntimeError("model down")
        if self.stop_after and stage == self.stop_after[0] and chapter_from == self.stop_after[1]:
            request_stop(self.repo, novel_id=novel_id)
        return SimpleNamespace(failed=2 if stage == self.fail else 0)


def ticking():
    t = [0.0]

    def clock() -> float:
        t[0] += 30.0
        return t[0]

    return clock


def test_runs_every_stage_in_order_with_chunks(repo: Repository) -> None:
    fake = FakeStages(repo)
    result = run_ingest(repo, novel_id="n", config=CONFIG, run_stage_fn=fake, chunk=50, clock=ticking(), pid=1)
    assert result.status == "done"
    assert [c[0] for c in fake.calls if c[0] not in ("s1", "s3", "s4")] == ["s0", "s2", "s5", "s6", "prefix", "s7"]
    assert [c for c in fake.calls if c[0] == "s1"] == [("s1", 1, 50), ("s1", 51, 100), ("s1", 101, 120)]
    assert [c[0] for c in fake.calls] == [s for s in STAGES for _ in range(3 if s in ("s1", "s3", "s4") else 1)]
    run = repo.get_pipeline_run(novel_id="n")
    assert run["status"] == "done" and run["finished_at"]
    assert set(json.loads(run["stage_times"])) == set(STAGES)
    assert json.loads(run["models"]) == {"s1_extractor": "s1m", "local_extractor": "loc"}


def test_stop_lands_after_current_chunk_and_resume_continues(repo: Repository) -> None:
    fake = FakeStages(repo, stop_after=("s1", 51))
    result = run_ingest(repo, novel_id="n", config=CONFIG, run_stage_fn=fake, chunk=50, clock=ticking(), pid=1)
    assert result.status == "stopped" and result.stage == "s1"
    assert fake.calls[-1] == ("s1", 51, 100)
    run = repo.get_pipeline_run(novel_id="n")
    assert run["status"] == "stopped" and run["current_stage"] == "s1"
    assert json.loads(run["progress"])["done_to"] == 100

    again = FakeStages(repo)
    resumed = run_ingest(repo, novel_id="n", config={}, resume=True, run_stage_fn=again, chunk=50,
                         clock=ticking(), pid=2)
    assert resumed.status == "done" and resumed.run_id == result.run_id
    assert again.calls[0] == ("s1", 1, 50)  # the stage reruns; done chapters are skipped inside it
    assert "s0" not in [c[0] for c in again.calls]
    assert resumed.stage_times["s1"] > result.stage_times["s1"]


def test_stop_before_a_whole_stage(repo: Repository) -> None:
    fake = FakeStages(repo, stop_after=("s2", 1))
    result = run_ingest(repo, novel_id="n", config=CONFIG, run_stage_fn=fake, clock=ticking(), pid=1)
    assert result.status == "stopped" and result.stage == "s3"
    assert fake.calls[-1][0] == "s2"


def test_failures_stop_the_run_and_resume_retries_that_stage(repo: Repository) -> None:
    fake = FakeStages(repo, fail="s1")
    result = run_ingest(repo, novel_id="n", config=CONFIG, run_stage_fn=fake, chunk=50, clock=ticking(), pid=1)
    assert result.status == "failed" and result.stage == "s1"
    assert result.error == "s1: 6 failed"  # every chunk still ran
    assert "s2" not in [c[0] for c in fake.calls]
    again = FakeStages(repo)
    assert run_ingest(repo, novel_id="n", config=CONFIG, resume=True, run_stage_fn=again, pid=1).status == "done"
    assert again.calls[0][0] == "s1"


def test_an_exception_is_recorded_on_the_run(repo: Repository) -> None:
    result = run_ingest(repo, novel_id="n", config=CONFIG, run_stage_fn=FakeStages(repo, boom="s6"), pid=1)
    assert result.status == "failed" and result.stage == "s6"
    assert repo.get_pipeline_run(novel_id="n")["error"] == "s6: RuntimeError: model down"


def test_single_flight_and_resume_rules(repo: Repository) -> None:
    repo.start_pipeline_run(novel_id="n", run_id="live", stages=list(STAGES), chapter_to=10, models={}, pid=7)
    with pytest.raises(IngestError, match="still running"):
        run_ingest(repo, novel_id="n", config=CONFIG, run_stage_fn=FakeStages(repo), alive=lambda pid: True)
    # A run whose process is gone does not block, and can be resumed
    result = run_ingest(repo, novel_id="n", config=CONFIG, resume=True, run_stage_fn=FakeStages(repo),
                        alive=lambda pid: False, pid=8)
    assert result.run_id == "live" and result.status == "done"
    with pytest.raises(IngestError, match="no unfinished run"):
        run_ingest(repo, novel_id="n", config=CONFIG, resume=True, run_stage_fn=FakeStages(repo))
    with pytest.raises(IngestError, match="no unfinished run"):
        run_ingest(repo, novel_id="other", config=CONFIG, resume=True, run_stage_fn=FakeStages(repo))


def test_resume_refuses_another_range(repo: Repository) -> None:
    run_ingest(repo, novel_id="n", config=CONFIG, run_stage_fn=FakeStages(repo, fail="s0"), pid=1)
    with pytest.raises(IngestError, match="covers chapters to 120"):
        run_ingest(repo, novel_id="n", config=CONFIG, chapter_to=60, resume=True, run_stage_fn=FakeStages(repo))


def test_stop_marks_only_running_runs_of_that_novel(repo: Repository) -> None:
    repo.start_pipeline_run(novel_id="a", run_id="ra", stages=["s0"], chapter_to=1, models={}, pid=1)
    repo.start_pipeline_run(novel_id="b", run_id="rb", stages=["s0"], chapter_to=1, models={}, pid=1)
    assert request_stop(repo, novel_id="a") == 1
    assert repo.get_pipeline_run(novel_id="a")["status"] == "stopping"
    assert repo.get_pipeline_run(novel_id="b")["status"] == "running"
    assert request_stop(repo, novel_id="a") == 0


def test_pid_alive() -> None:
    assert pid_alive(os.getpid())
    assert not pid_alive(None)
    assert not pid_alive(0)


def test_status_and_stop_commands(tmp_path: Path) -> None:
    db = tmp_path / "xn.db"
    conn = connect(db)
    init_schema(conn)
    repo = Repository(conn)
    repo.add_novel(novel_id="n", title="T", genre_profile="xianxia", total_chapters=3)
    repo.upsert_job(novel_id="n", chapter_id=1, stage="s1", status="done")
    repo.upsert_job(novel_id="n", chapter_id=2, stage="s1", status="failed", error="schema")
    repo.start_pipeline_run(novel_id="n", run_id="r1", stages=["s0"], chapter_to=3, models={"s1": "m"},
                            pid=os.getpid())
    conn.close()
    runner = CliRunner()
    out = runner.invoke(app, ["status", "--novel", "n", "--db", str(db)])
    assert out.exit_code == 0, out.output
    assert "run r1: running" in out.output
    assert "jobs s1: done=1 failed=1" in out.output
    assert "failed s1 ch2 attempts=0: schema" in out.output
    assert "cloud spend: today $0.000, total $0.000" in out.output
    assert runner.invoke(app, ["stop", "--novel", "n", "--db", str(db)]).exit_code == 0
    assert "stopping" in runner.invoke(app, ["status", "--novel", "n", "--db", str(db)]).output
    assert runner.invoke(app, ["stop", "--novel", "n", "--db", str(db)]).exit_code == 1
