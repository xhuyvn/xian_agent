"""Run every pipeline stage in order as one stoppable, resumable run (G-29).

The run is a row in `pipeline_runs`. `request_stop` marks it `stopping`; the driver checks
that before every chunk of s1, s3 and s4 and before every other stage, then marks it
`stopped`. A resume reruns the stage it stopped in: each stage skips work already done.
"""

from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
import sys
import threading
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


def _parallel_chunks(
    *,
    ranges: list[tuple[int, int]],
    workers: int,
    open_repo: Callable[[], tuple[Any, Repository]],
    work: Callable[[Repository, int, int], Any],
    stop_asked: Callable[[Repository], bool],
    progress: Callable[[Repository, int, int], None],
) -> tuple[int, bool, str | None]:
    """Run chunks in worker threads, each on its own connection; returns (failed, stopped, error).

    A worker takes the next chunk only while no stop was asked and no worker raised, so a stop
    or an error ends the stage after the chunks already running.
    """
    lock = threading.Lock()
    pending = list(ranges)
    state = {"failed": 0, "done": 0, "stopped": False, "error": None}

    def worker() -> None:
        conn, wrepo = open_repo()
        try:
            while True:
                with lock:
                    if state["error"] or state["stopped"] or not pending:
                        return
                if stop_asked(wrepo):
                    with lock:
                        state["stopped"] = bool(pending)
                    return
                with lock:
                    if not pending:
                        return
                    lo, hi = pending.pop(0)
                try:
                    result = work(wrepo, lo, hi)
                except Exception as err:  # the first error is kept; the others finish their chunk
                    with lock:
                        state["error"] = state["error"] or f"{type(err).__name__}: {err}"
                    return
                with lock:
                    state["failed"] += getattr(result, "failed", 0) or 0
                    state["done"] += 1
                    done, failed = state["done"], state["failed"]
                progress(wrepo, done, failed)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(worker) for _ in range(min(workers, len(ranges)))]:
            future.result()
    return state["failed"], state["stopped"], state["error"]


def run_ingest(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    chapter_to: int | None = None,
    resume: bool = False,
    chunk: int = DEFAULT_CHUNK,
    workers: int = 1,
    open_repo: Callable[[], tuple[Any, Repository]] | None = None,
    stage_kwargs: dict[str, Any] | None = None,
    run_stage_fn: Callable[..., Any] = run_stage,
    before_stage: Callable[[str], None] | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
    clock: Callable[[], float] = time.monotonic,
    pid: int | None = None,
    until: str | None = None,
) -> IngestResult:
    """Run s0 to s7 for a novel, or resume its last unfinished run."""
    if chunk < 1:
        raise IngestError(f"chunk must be at least 1, got {chunk}")
    if until is not None and until not in STAGES:
        raise IngestError(f"until must be one of {', '.join(STAGES)}, got {until}")
    if workers < 1:
        raise IngestError(f"workers must be at least 1, got {workers}")
    if workers > 1 and open_repo is None:
        raise IngestError("workers > 1 needs open_repo, one connection per worker")
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
            repo.update_pipeline_run(novel_id=novel_id, run_id=run_id, current_stage=stage)  # resume starts here
            return finish("stopped", stage)
        repo.update_pipeline_run(novel_id=novel_id, run_id=run_id, current_stage=stage, progress={"stage": stage})
        began = clock()
        failed = 0
        try:
            if before_stage is not None:
                before_stage(stage)
            if stage == "s1" and workers > 1:
                ranges = [(lo, min(lo + chunk - 1, chapter_to)) for lo in range(1, chapter_to + 1, chunk)]
                failed, stopped, error = _parallel_chunks(
                    ranges=ranges, workers=workers, open_repo=open_repo,  # type: ignore[arg-type]
                    work=lambda wrepo, lo, hi: run_stage_fn(
                        wrepo, stage=stage, novel_id=novel_id, config=config, chapter_from=lo, chapter_to=hi, **kwargs
                    ),
                    stop_asked=lambda wrepo: (
                        (row := wrepo.get_pipeline_run(novel_id=novel_id, run_id=run_id)) is not None
                        and row["status"] == "stopping"
                    ),
                    progress=lambda wrepo, done, nfailed: wrepo.update_pipeline_run(
                        novel_id=novel_id, run_id=run_id,
                        progress={"stage": stage, "chunks_done": done, "chunks": len(ranges), "failed": nfailed,
                                  "workers": workers},
                    ),
                )
                if error is not None or stopped:
                    stage_times[stage] = stage_times.get(stage, 0.0) + clock() - began
                    if error is not None:
                        return finish("failed", stage, f"{stage}: {error}")
                    return finish("stopped", stage)
            elif stage in CHUNKED:
                for lo in range(1, chapter_to + 1, chunk):
                    hi = min(lo + chunk - 1, chapter_to)
                    if lo > 1 and stop_asked():
                        stage_times[stage] = stage_times.get(stage, 0.0) + clock() - began
                        return finish("stopped", stage)
                    result = run_stage_fn(
                        repo, stage=stage, novel_id=novel_id, config=config, chapter_from=lo, chapter_to=hi, **kwargs
                    )
                    failed += getattr(result, "failed", 0) or 0
                    stopped_at = getattr(result, "stopped_at", None)
                    repo.update_pipeline_run(
                        novel_id=novel_id, run_id=run_id,
                        progress={"stage": stage, "done_to": hi, "to": chapter_to, "failed": failed},
                    )
                    if stopped_at is not None:  # s3 and s4 stop at a failure; later chunks would leave a gap
                        failed = max(failed, 1)
                        break
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
        if stage == until and stage != stages[-1]:  # planned pause: resume starts at the next stage
            following = stages[stages.index(stage) + 1]
            repo.update_pipeline_run(novel_id=novel_id, run_id=run_id, current_stage=following)
            return finish("stopped", following)
    return finish("done", None)
