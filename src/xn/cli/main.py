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

from xn.config import load_config
from xn.graph.deps import GraphDeps, QueryError
from xn.graph.workflow import ask as ask_graph, build_graph, open_checkpointer, thread_novel
from xn.llm import EgressError, SpendCapError, UpstreamError
from xn.pipeline.runner import RunRequestError, run_stage
from xn.pipeline.s0_parse import ChapterSetError
from xn.pipeline.s2_resolve import S2StaleError
from xn.pipeline.s4_embed import S4RequestError
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
    for attr in ("processed", "written", "skipped", "failed", "points"):
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
    stage: str = typer.Option(..., "--stage", help="Stage to run (s0, s1, s2, s3, s4)"),
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
    except (ChapterSetError, S4RequestError) as err:
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
