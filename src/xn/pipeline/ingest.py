"""Run every pipeline stage in order as one stoppable, resumable run (G-29).

The run is a row in `pipeline_runs`. `request_stop` marks it `stopping`; the driver checks
that before every chunk of s1, s3 and s4 and before every other stage, then marks it
`stopped`. A resume reruns the stage it stopped in: each stage skips work already done.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import json
import os
import sys
import time
from typing import Any, Callable
import uuid

from xn.pipeline.runner import STAGES, run_stage
from xn.storage.repository import Repository

CHUNKED = ("s1", "s3", "s4")  # stages that take --from, run in chunks so a stop lands soon
DEFAULT_CHUNK = 50
LIVE = ("running", "stopping")


class IngestError(ValueError):
    """Raised when an ingest is refused before any stage runs."""


@dataclass
class IngestResult:
    run_id: str
    status: str  # stopped | done | failed
    stage: str | None
    stage_times: dict[str, float] = field(default_factory=dict)
    error: str | None = None


def pid_alive(pid: int | None) -> bool:
    """True when a process with this id is running; never signals it."""
    if not pid:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def live_run(repo: Repository, *, novel_id: str, alive: Callable[[int | None], bool] = pid_alive) -> Any:
    """The novel's latest run if it is running or stopping in a live process, else None."""
    run = repo.get_pipeline_run(novel_id=novel_id)
    if run is not None and run["status"] in LIVE and alive(run["pid"]):
        return run
    return None


def request_stop(repo: Repository, *, novel_id: str) -> int:
    """Ask the novel's running ingest to stop at its next safe point."""
    return repo.request_pipeline_stop(novel_id=novel_id)


def _models(config: dict[str, Any]) -> dict[str, Any]:
    """Every configured model by its config key, as served, so the run records what it used."""
    return {
        key: spec.get("serving_name") or spec.get("name")
        for key, spec in (config.get("models") or {}).items()
        if isinstance(spec, dict)
    }


def run_ingest(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    chapter_to: int | None = None,
    resume: bool = False,
    chunk: int = DEFAULT_CHUNK,
    stage_kwargs: dict[str, Any] | None = None,
    run_stage_fn: Callable[..., Any] = run_stage,
    before_stage: Callable[[str], None] | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
    clock: Callable[[], float] = time.monotonic,
    pid: int | None = None,
) -> IngestResult:
    """Run s0 to s7 for a novel, or resume its last unfinished run."""
    if chunk < 1:
        raise IngestError(f"chunk must be at least 1, got {chunk}")
    pid = os.getpid() if pid is None else pid
    current = live_run(repo, novel_id=novel_id, alive=alive)
    if current is not None:
        raise IngestError(f"run {current['run_id']} is still {current['status']} in process {current['pid']}")

    stages = list(STAGES)
    if resume:
        prev = repo.get_pipeline_run(novel_id=novel_id)
        if prev is None or prev["status"] == "done":
            raise IngestError(f"no unfinished run to resume for {novel_id}")
        if chapter_to is not None and chapter_to != prev["chapter_to"]:
            raise IngestError(f"run {prev['run_id']} covers chapters to {prev['chapter_to']}, not {chapter_to}")
        run_id, chapter_to = prev["run_id"], prev["chapter_to"]
        stages = json.loads(prev["stages"])
        start = stages.index(prev["current_stage"]) if prev["current_stage"] in stages else 0
        stage_times = json.loads(prev["stage_times"] or "{}")
        repo.update_pipeline_run(novel_id=novel_id, run_id=run_id, status="running", pid=pid, error=None)
    else:
        chapter_to = chapter_to or config.get("total_chapters")
        if not chapter_to:
            raise IngestError("chapter_to is not given and the novel config has no total_chapters")
        run_id, start, stage_times = uuid.uuid4().hex, 0, {}
        repo.start_pipeline_run(
            novel_id=novel_id, run_id=run_id, stages=stages, chapter_to=chapter_to, models=_models(config), pid=pid
        )

    def finish(status: str, stage: str | None, error: str | None = None) -> IngestResult:
        repo.update_pipeline_run(novel_id=novel_id, run_id=run_id, status=status, stage_times=stage_times, error=error)
        return IngestResult(run_id=run_id, status=status, stage=stage, stage_times=stage_times, error=error)

    def stop_asked() -> bool:
        run = repo.get_pipeline_run(novel_id=novel_id, run_id=run_id)
        return run is not None and run["status"] == "stopping"

    kwargs = stage_kwargs or {}
    for stage in stages[start:]:
        if stop_asked():
            return finish("stopped", stage)
        repo.update_pipeline_run(novel_id=novel_id, run_id=run_id, current_stage=stage, progress={"stage": stage})
        began = clock()
        failed = 0
        try:
            if before_stage is not None:
                before_stage(stage)
            if stage in CHUNKED:
                for lo in range(1, chapter_to + 1, chunk):
                    hi = min(lo + chunk - 1, chapter_to)
                    if lo > 1 and stop_asked():
                        stage_times[stage] = stage_times.get(stage, 0.0) + clock() - began
                        return finish("stopped", stage)
                    result = run_stage_fn(
                        repo, stage=stage, novel_id=novel_id, config=config, chapter_from=lo, chapter_to=hi, **kwargs
                    )
                    failed += getattr(result, "failed", 0) or 0
                    repo.update_pipeline_run(
                        novel_id=novel_id, run_id=run_id,
                        progress={"stage": stage, "done_to": hi, "to": chapter_to, "failed": failed},
                    )
            else:
                result = run_stage_fn(repo, stage=stage, novel_id=novel_id, config=config, chapter_to=chapter_to, **kwargs)
                failed = getattr(result, "failed", 0) or 0
                if getattr(result, "stopped_at", None) is not None:
                    failed = max(failed, 1)
        except Exception as err:  # recorded on the run, then reported by the caller
            stage_times[stage] = stage_times.get(stage, 0.0) + clock() - began
            return finish("failed", stage, f"{stage}: {type(err).__name__}: {err}")
        stage_times[stage] = stage_times.get(stage, 0.0) + clock() - began
        if failed:
            return finish("failed", stage, f"{stage}: {failed} failed")
    return finish("done", None)
