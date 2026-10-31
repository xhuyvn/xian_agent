"""CLI entrypoint for XN-Agent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import typer

from xn.config import load_config
from xn.pipeline.runner import RunRequestError, run_stage
from xn.pipeline.s0_parse import ChapterSetError
from xn.pipeline.s2_resolve import S2StaleError
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
    for attr in ("processed", "written", "skipped", "failed"):
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
    stage: str = typer.Option(..., "--stage", help="Stage to run (s0, s1, s2, s3)"),
    chapter_from: int = typer.Option(1, "--from", help="Starting chapter number"),
    chapter_to: int | None = typer.Option(None, "--to", help="Ending chapter number"),
    stale_only: bool = typer.Option(False, "--stale-only", help="Process only stale chapters"),
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild stage resolution (s2 only)"),
    db: Path = typer.Option(Path("data/xn.db"), "--db", help="Path to SQLite database file"),
    raw_dir: Path | None = typer.Option(
        None, "--raw-dir", help="Path to raw chapters directory (default: data/novels/<novel>/raw)"
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
    except ChapterSetError as err:
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
