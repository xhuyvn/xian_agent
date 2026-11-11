"""CLI entrypoint for XN-Agent."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
from typing import Any
import uuid
import typer

from pydantic_ai.exceptions import ModelAPIError

from datetime import datetime, timezone
import json

from xn.config import load_config
from xn import costs as run_costs_mod
from xn import estimate as estimate_mod
from xn.embed import OllamaEmbedder
from xn.pipeline.ingest import DEFAULT_CHUNK, IngestError, pid_alive, request_stop, run_ingest
from xn.graph.deps import GraphDeps, QueryError
from xn.graph.workflow import ask as ask_graph, build_graph, open_checkpointer, thread_novel
from xn.llm import EgressError, SpendCapError, UpstreamError
from xn.pipeline.runner import RunRequestError, run_stage
from xn.pipeline.s0_parse import ChapterSetError
from xn.pipeline.s2_resolve import S2StaleError
from xn.pipeline.s4_embed import S4RequestError
from xn.pipeline.s5_segment import S5RequestError
from xn.pipeline.s7_digest import S7RequestError
from xn.pipeline.s6_synthesize import S6RequestError
from xn.pipeline.prefix_summaries import PrefixRequestError
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

app = typer.Typer(help="XN-Agent CLI")


@app.callback()
def callback() -> None:
    """XN-Agent command-line interface."""
    pass


def format_result(stage: str, result: Any) -> str:
    """Format stage execution result on one line."""
    parts = [f"{stage}:"]
    for attr in ("processed", "written", "skipped", "failed", "points", "arcs", "shifted", "review_failed",
                 "cost_usd", "pruned", "full", "prefix"):
        val = getattr(result, attr, None)
        if val is not None:
            parts.append(f"{attr}={val}")
    stopped_at = getattr(result, "stopped_at", None)
    if stopped_at is not None:
        parts.append(f"stopped_at={stopped_at}")
    return " ".join(parts)


@app.command("run")
def run(
    novel: str = typer.Option(..., "--novel", help="Novel ID"),
    stage: str = typer.Option(..., "--stage", help="Stage to run (s0, s1, s2, s3, s4, s5, s6, prefix, s7)"),
    chapter_from: int = typer.Option(1, "--from", help="Starting chapter number"),
    chapter_to: int | None = typer.Option(None, "--to", help="Ending chapter number"),
    stale_only: bool = typer.Option(False, "--stale-only", help="Process only stale chapters"),
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild stage resolution (s2 only)"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file"),
    raw_dir: Path | None = typer.Option(
        None, "--raw-dir", help="Path to raw chapters directory (default: raw_dir in the novel config, else data/novels/<novel>/raw)"
    ),
    configs_dir: Path = typer.Option(Path("configs"), "--configs-dir", help="Path to configs directory"),
    prompts_dir: Path = typer.Option(Path("prompts"), "--prompts-dir", help="Path to prompts directory"),
) -> None:
    """Run a pipeline stage for a novel."""
    logging.basicConfig(level=logging.INFO)

    try:
        config = load_config(novel, configs_dir=configs_dir)
    except FileNotFoundError as err:
        typer.echo(f"Error: {err}")
        raise typer.Exit(code=1)

    if raw_dir is None:
        raw_dir = Path(config.get("raw_dir") or Path("data/novels") / novel / "raw")

    db_path = Path(db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)

    try:
        init_schema(conn)
        repo = Repository(conn)
        result = run_stage(
            repo,
            stage=stage,
            novel_id=novel,
            config=config,
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            stale_only=stale_only,
            rebuild=rebuild,
            raw_dir=raw_dir,
            prompts_dir=prompts_dir,
        )
    except RunRequestError as err:
        typer.echo(f"Error: {err}")
        raise typer.Exit(code=1)
    except S2StaleError as err:
        typer.echo(f"Error: {err}. Use --rebuild to redo the novel.")
        raise typer.Exit(code=1)
    except (ChapterSetError, S4RequestError, S5RequestError, S6RequestError, PrefixRequestError) as err:
        typer.echo(f"Error: {err}")
        raise typer.Exit(code=1)
    except S7RequestError as err:
        typer.echo(f"Error: {err}")
        raise typer.Exit(code=1)
    except FileNotFoundError as err:
        typer.echo(f"Error: {err}")
        raise typer.Exit(code=1)
    finally:
        conn.close()

    typer.echo(format_result(stage, result))
    failed = getattr(result, "failed", 0)
    stopped_at = getattr(result, "stopped_at", None)
    if failed > 0 or stopped_at is not None:
        raise typer.Exit(code=1)


def _open_repo(db: Path) -> tuple[Any, Repository]:
    db_path = Path(db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_schema(conn)
    return conn, Repository(conn)


@app.command("ingest")
def ingest(
    novel: str = typer.Option(..., "--novel", help="Novel ID"),
    chapter_to: int | None = typer.Option(None, "--to", help="Last chapter (default: total_chapters)"),
    resume: bool = typer.Option(False, "--resume", help="Continue the novel's last stopped or failed run"),
    chunk: int = typer.Option(DEFAULT_CHUNK, "--chunk", help="Chapters per chunk on s1, s3 and s4"),
    workers: int = typer.Option(1, "--workers", help="S1 chunks at once, one connection each (match Ollama's slots)"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file"),
    raw_dir: Path | None = typer.Option(None, "--raw-dir", help="Raw chapters (default: raw_dir in the novel config)"),
    configs_dir: Path = typer.Option(Path("configs"), "--configs-dir", help="Path to configs directory"),
    prompts_dir: Path = typer.Option(Path("prompts"), "--prompts-dir", help="Path to prompts directory"),
    until: str | None = typer.Option(None, "--until", help="Stop after this stage; --resume continues with the next"),
) -> None:
    """Run s0 to s7 in order as one run that `xn stop` can stop and `--resume` continues."""
    logging.basicConfig(level=logging.INFO)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        config = load_config(novel, configs_dir=configs_dir)
    except FileNotFoundError as err:
        raise _fail("NOVEL_NOT_FOUND", err)
    if raw_dir is None:
        raw_dir = Path(config.get("raw_dir") or Path("data/novels") / novel / "raw")

    def before_stage(stage: str) -> None:
        if stage == "s4":  # S4 refuses while another model holds the GPU
            unloaded = OllamaEmbedder.from_config(config).unload_others()
            if unloaded:
                typer.echo(f"unloaded before s4: {', '.join(unloaded)}")
        typer.echo(f"stage {stage} starting")

    conn, repo = _open_repo(db)
    try:
        result = run_ingest(
            repo, novel_id=novel, config=config, chapter_to=chapter_to, resume=resume, chunk=chunk,
            workers=workers, open_repo=lambda: _open_repo(db),
            stage_kwargs={"raw_dir": raw_dir, "prompts_dir": prompts_dir}, before_stage=before_stage,
            until=until,
        )
    except IngestError as err:
        raise _fail("VALIDATION_FAILED", err)
    finally:
        conn.close()
    _export_costs(db, result.run_id)
    times = " ".join(f"{s}={t / 60:.1f}m" for s, t in result.stage_times.items())
    typer.echo(f"run {result.run_id}: {result.status}" + (f" at {result.stage}" if result.stage else ""))
    typer.echo(f"stage times: {times or 'none'}")
    if result.error:
        typer.echo(f"Error: {result.error}")
    if result.status != "done":
        raise typer.Exit(code=1)


@app.command("stop")
def stop(
    novel: str = typer.Option(..., "--novel", help="Novel ID"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file"),
) -> None:
    """Ask a running ingest to stop after its current chunk or stage."""
    conn, repo = _open_repo(db)
    try:
        marked = request_stop(repo, novel_id=novel)
    finally:
        conn.close()
    if not marked:
        raise _fail("NOT_RUNNING", f"no running ingest for {novel}")
    typer.echo(f"stop requested for {novel}; it stops after the current chunk or stage")


@app.command("status")
def status(
    novel: str = typer.Option(..., "--novel", help="Novel ID"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file"),
) -> None:
    """Show the novel's last ingest run, jobs per stage, failed chapters and cloud spend."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    conn, repo = _open_repo(db)
    try:
        run = repo.get_pipeline_run(novel_id=novel)
        counts, failed = repo.job_summary(novel_id=novel)
        today = datetime.now(timezone.utc).date().isoformat()
        spend_today = repo.novel_cloud_spend(novel_id=novel, since=today)
        spend_total = repo.novel_cloud_spend(novel_id=novel)
    finally:
        conn.close()
    if run is None:
        typer.echo(f"{novel}: no ingest run yet")
    else:
        state = run["status"]
        if state in ("running", "stopping") and not pid_alive(run["pid"]):
            state += " (process gone; resume with --resume)"
        typer.echo(f"run {run['run_id']}: {state}, stage {run['current_stage']}, to chapter {run['chapter_to']}")
        typer.echo(f"progress: {run['progress']}  models: {run['models']}")
        times = json.loads(run["stage_times"] or "{}")
        typer.echo("stage times: " + (" ".join(f"{s}={t / 60:.1f}m" for s, t in times.items()) or "none"))
        typer.echo(f"started {run['started_at']}, updated {run['updated_at']}, finished {run['finished_at']}")
        if run["error"]:
            typer.echo(f"error: {run['error']}")
    by_stage: dict[str, dict[str, int]] = {}
    for row in counts:
        by_stage.setdefault(row["stage"], {})[row["status"]] = row["n"]
    for stage, states in by_stage.items():
        typer.echo(f"jobs {stage}: " + " ".join(f"{k}={v}" for k, v in sorted(states.items())))
    for row in failed:
        typer.echo(f"failed {row['stage']} ch{row['chapter_id']} attempts={row['attempts']}: {row['error']}")
    typer.echo(f"cloud spend: today ${spend_today:.3f}, total ${spend_total:.3f}")


def _export_costs(db: Path, run_id: str) -> None:
    """Write data/runs/<run_id>-costs.json next to the database; a failure here never fails the run (G-33)."""
    try:
        conn = run_costs_mod.open_readonly(db)
        try:
            summary = run_costs_mod.run_costs(conn, run_id)
        finally:
            conn.close()
        path = run_costs_mod.write_costs(summary, Path(db).parent / "runs")
        typer.echo(f"costs: {path}")
    except Exception as err:  # noqa: BLE001 - the export is a report, not part of the run
        typer.echo(f"costs not written: {err}")


def _latest_run_id(conn: Any, novel: str | None) -> str:
    row = conn.execute(
        "SELECT run_id FROM pipeline_runs WHERE (? IS NULL OR novel_id = ?) ORDER BY started_at DESC LIMIT 1",
        (novel, novel),
    ).fetchone()
    if row is None:
        raise ValueError("no pipeline run in the database")
    return str(row["run_id"])


@app.command("costs")
def costs(
    run_id: str | None = typer.Option(None, "--run", help="Pipeline run id (default: the latest run)"),
    novel: str | None = typer.Option(None, "--novel", help="Pick the latest run of this novel"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file (opened read-only)"),
    out_dir: Path | None = typer.Option(None, "--out-dir", help="Where to write <run_id>-costs.json (default: runs/ next to the database)"),
    no_write: bool = typer.Option(False, "--no-write", help="Print only; write no JSON file"),
    gap_s: float = typer.Option(run_costs_mod.DEFAULT_GAP_S, "--gap", help="Seconds of quiet that count as a pause"),
) -> None:
    """Cost and time of an ingest run per stage and model; never writes to the database."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        conn = run_costs_mod.open_readonly(db)
    except FileNotFoundError as err:
        raise _fail("NOT_FOUND", err)
    try:
        summary = run_costs_mod.run_costs(conn, run_id or _latest_run_id(conn, novel), gap_s=gap_s)
    except ValueError as err:
        raise _fail("NOT_FOUND", err)
    finally:
        conn.close()
    typer.echo(run_costs_mod.format_costs(summary))
    if not no_write:
        path = run_costs_mod.write_costs(summary, out_dir or Path(db).parent / "runs")
        typer.echo(f"written: {path}")


def _estimate_config(novel: str, configs_dir: Path) -> dict[str, Any]:
    try:
        return load_config(novel, configs_dir=configs_dir)
    except FileNotFoundError:  # a novel with no config yet: the global config has the pools
        import yaml

        return yaml.safe_load((Path(configs_dir) / "config.yaml").read_text(encoding="utf-8")) or {}


@app.command("estimate")
def estimate(
    chapters: int | None = typer.Option(None, "--chapters", help="Chapters to forecast"),
    raw_dir: Path | None = typer.Option(None, "--raw-dir", help="Raw chapter_N.md files to forecast (by characters)"),
    source: Path | None = typer.Option(None, "--from", help="A <run_id>-costs.json to scale (default: measure --run in --db)"),
    run_id: str | None = typer.Option(None, "--run", help="Pipeline run to scale (default: the latest run)"),
    novel: str | None = typer.Option(None, "--novel", help="Novel whose config names the pools (default: the run's)"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file (opened read-only)"),
    extra_db: list[Path] = typer.Option([], "--extra-db", help="Other databases whose pool tokens count against the quota"),
    configs_dir: Path = typer.Option(Path("configs"), "--configs-dir", help="Path to configs directory"),
) -> None:
    """Forecast time, tokens per model and cost per stage, and the free pool quota left."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if (chapters is None) == (raw_dir is None):
        raise _fail("VALIDATION_FAILED", "give exactly one of --chapters or --raw-dir")
    if chapters is not None and chapters <= 0:
        raise _fail("VALIDATION_FAILED", "--chapters must be positive")
    conns = []
    try:
        try:
            conns = [run_costs_mod.open_readonly(p) for p in [db, *extra_db]]
        except FileNotFoundError as err:
            raise _fail("NOT_FOUND", err)
        try:
            if source is not None:
                summary = json.loads(Path(source).read_text(encoding="utf-8"))
            else:
                summary = run_costs_mod.run_costs(conns[0], run_id or _latest_run_id(conns[0], novel))
        except (ValueError, FileNotFoundError) as err:
            raise _fail("NOT_FOUND", err)
        if not summary.get("chapters"):
            raise _fail("VALIDATION_FAILED", f"run {summary.get('run_id')} has no chapters to scale from")
        config = _estimate_config(novel or summary["novel_id"], configs_dir)
        quota = estimate_mod.quota_left(config, conns)
    finally:
        for c in conns:
            c.close()
    n_chars = None
    if raw_dir is not None:
        try:
            chapters, n_chars = estimate_mod.raw_dir_size(raw_dir)
        except (OSError, ValueError) as err:
            raise _fail("NOT_FOUND", err)
    forecast = estimate_mod.estimate(summary, config, quota, chapters=int(chapters), chars=n_chars)
    typer.echo(estimate_mod.format_estimate(forecast))


def format_answer(state: dict[str, Any]) -> str:
    """Format a query result; the resolved bound leads every answer and refusal."""
    lines = []
    if state.get("chapter_max") is not None:
        lines.append(f"Phạm vi: chương 1–{state['chapter_max']}")

    if state.get("refusal"):
        lines.append(str(state["refusal"]))
        for mention, names in (state.get("suggestions") or {}).items():
            if names:
                lines.append(f"Không tìm thấy \"{mention}\". Có phải: {', '.join(names)}?")
        return "\n".join(lines)

    answer = state["answer"]
    lines.append(answer.markdown)
    cited = ", ".join(str(c) for c in answer.citations) or "không có"
    lines.append(f"Trích dẫn: {cited}")
    lines.append(f"Độ bám nguồn: {state.get('grounding_ratio') or 0.0:.0%}")
    return "\n".join(lines)


def _fail(code: str, message: object) -> typer.Exit:
    """Print a coded error and return the exit to raise."""
    typer.echo(f"Error [{code}]: {message}")
    return typer.Exit(code=1)


@app.command("progress")
def progress(
    novel: str = typer.Argument(..., help="Novel ID"),
    chapter: int = typer.Argument(..., help="Last chapter read; the default spoiler bound"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file"),
) -> None:
    """Save reading progress, the bound used when a question gives none (§3.4 L1)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if chapter < 1:
        raise _fail("VALIDATION_FAILED", f"Chapter must be at least 1: {chapter}")

    conn = connect(Path(db))
    try:
        init_schema(conn)
        repo = Repository(conn)
        record = repo.get_novel(novel_id=novel)
        if record is None:
            raise _fail("NOVEL_NOT_FOUND", f"Novel not found: {novel}")
        total = record["total_chapters"]
        if chapter > total:
            raise _fail("VALIDATION_FAILED", f"Chapter {chapter} is above total_chapters {total}")
        repo.set_reader_progress(novel_id=novel, chapter=chapter)
    finally:
        conn.close()

    typer.echo(f"Tiến độ đọc {novel}: chương {chapter}/{total}")


@app.command("ask")
def ask(
    question: str = typer.Argument(..., help="Question about the novel"),
    novel: str | None = typer.Option(
        None, "--novel", help="Novel ID (a follow-up inherits it from the thread)"
    ),
    until: int | None = typer.Option(None, "--until", help="Spoiler bound: read up to this chapter"),
    full: bool = typer.Option(False, "--full", help="Remove the spoiler bound explicitly"),
    thread: str | None = typer.Option(None, "--thread", help="Continue this conversation"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file"),
    checkpoints: Path = typer.Option(
        Path("data/graph_checkpoints.db"), "--checkpoints", help="Path to graph checkpoint file"
    ),
    configs_dir: Path = typer.Option(Path("configs"), "--configs-dir", help="Path to configs directory"),
    prompts_dir: Path = typer.Option(Path("prompts"), "--prompts-dir", help="Path to prompts directory"),
) -> None:
    """Ask a spoiler-bounded question, answered in compact mode from the ledger."""
    logging.basicConfig(level=logging.WARNING)
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if full and until is not None:
        raise _fail("VALIDATION_FAILED", "Use either --until or --full, not both")

    thread_id = thread or uuid.uuid4().hex
    saver = open_checkpointer(checkpoints)
    saved_novel = thread_novel(saver, thread_id)
    if novel and saved_novel and novel != saved_novel:
        saver.conn.close()
        raise _fail("VALIDATION_FAILED", f"Thread {thread_id} belongs to novel {saved_novel}")
    novel = novel or saved_novel
    if not novel:
        saver.conn.close()
        raise _fail("VALIDATION_FAILED", "--novel is required for a new thread")

    try:
        config = load_config(novel, configs_dir=configs_dir)
    except FileNotFoundError as err:
        saver.conn.close()
        raise _fail("NOVEL_NOT_FOUND", err)

    db_path = Path(db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        init_schema(conn)
        repo = Repository(conn)
        record = repo.get_novel(novel_id=novel)
        if record is None:
            raise QueryError("NOVEL_NOT_FOUND", f"Novel not found: {novel}")
        bound_to = record["total_chapters"] if full else until
        graph = build_graph(
            GraphDeps(repo=repo, config=config, prompts_dir=prompts_dir), saver
        )
        state = ask_graph(
            graph, thread_id=thread_id, novel_id=novel, query=question, until=bound_to
        )
    except QueryError as err:
        raise _fail(err.code, err)
    except UpstreamError as err:
        raise _fail(err.code, err)
    except SpendCapError as err:
        raise _fail("SPEND_CAP", err)
    except EgressError as err:
        raise _fail("EGRESS_REFUSED", err)
    except ModelAPIError as err:
        raise _fail("UPSTREAM_ERROR", err)
    finally:
        conn.close()
        saver.conn.close()

    typer.echo(format_answer(state))
    typer.echo(f"Thread: {thread_id}")
