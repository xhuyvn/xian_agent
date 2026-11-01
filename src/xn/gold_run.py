"""Gold tuning runs (G-10.1): run real S1 on gold chapters, count drops, score dev only."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import copy
import json
import logging
from pathlib import Path
from typing import Any, Iterator

import typer

from xn.config import load_config
from xn.gold import GoldSet, gold_split, load_gold, score_extraction
from xn.pipeline import s1_extract
from xn.pipeline.s0_parse import run_s0
from xn.pipeline.s1_extract import run_s1
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

LOW_EVIDENCE = "low_evidence"
INVALID_CATEGORY = "invalid_category"


class _DropRecorder(logging.Handler):
    """Collect the events S1 drops, which it logs but does not store."""

    def __init__(self) -> None:
        super().__init__()
        self.drops: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if "dropping event" not in record.msg:
            return
        chapter_id, name = record.args[:2]  # type: ignore[index]
        reason = LOW_EVIDENCE if "low evidence" in record.msg else INVALID_CATEGORY
        self.drops.append({"chapter_id": chapter_id, "name": name, "reason": reason})


@contextmanager
def recorded_drops() -> Iterator[list[dict[str, Any]]]:
    """Yield a list that fills with S1's dropped events while the block runs."""
    recorder = _DropRecorder()
    s1_extract.logger.addHandler(recorder)
    try:
        yield recorder.drops
    finally:
        s1_extract.logger.removeHandler(recorder)


def summarize(
    gold: GoldSet, drops: list[dict[str, Any]], payloads: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """Count kept and dropped events per split; only dev names and scores are shown."""
    dev, held_out = gold_split(gold.chapters)
    dev_ids = {c.chapter_id for c in dev}
    held_ids = {c.chapter_id for c in held_out}

    def counts(ids: set[int]) -> dict[str, Any]:
        reasons = Counter(d["reason"] for d in drops if d["chapter_id"] in ids)
        return {
            "kept": sum(len(p["events"]) for n, p in payloads.items() if n in ids),
            "dropped": dict(reasons),
        }

    result = {
        "dev": counts(dev_ids),
        "held_out": counts(held_ids),
        "dev_dropped": [d for d in drops if d["chapter_id"] in dev_ids],
    }
    if dev_ids <= set(payloads):
        result["dev_score"] = score_extraction(dev, payloads)
    return result


def run_gold_chapters(
    repo: Repository,
    *,
    gold: GoldSet,
    config: dict[str, Any],
    prompt: str,
    only_dev: bool = False,
    model: Any = None,
    prompts_dir: Path = Path("prompts"),
) -> dict[str, Any]:
    """Run S1 at a prompt version on the gold chapters and summarize the result."""
    novel_id = config["novel_id"]
    dev, _ = gold_split(gold.chapters)
    chapters = {c.chapter_id for c in (dev if only_dev else gold.chapters)}
    cfg = copy.deepcopy(config)
    cfg["prompts"] = {**(cfg.get("prompts") or {}), "s1_extract": prompt}
    with recorded_drops() as drops:
        result = run_s1(
            repo, novel_id=novel_id, config=cfg, chapter_from=min(chapters),
            chapter_to=max(chapters), only_chapters=chapters, model=model, prompts_dir=prompts_dir,
        )
    rows = repo.list_extractions(novel_id=novel_id, chapter_max=max(chapters))
    payloads = {r["chapter_id"]: json.loads(r["payload"]) for r in rows if r["chapter_id"] in chapters}
    return {
        "prompt": prompt,
        "written": result.written,
        "failed": result.failed,
        **summarize(gold, drops, payloads),
    }


def main(
    db: Path = typer.Option(..., "--db", help="Database for this attempt; S0 fills it if new"),
    raw_dir: Path = typer.Option(..., "--raw-dir", help="Path to raw chapters directory"),
    out: Path = typer.Option(..., "--out", help="JSON report path"),
    prompt: str = typer.Option("v1", "--prompt", help="s1_extract prompt version"),
    only_dev: bool = typer.Option(False, "--only-dev", help="Run the dev chapters only"),
    novel: str = typer.Option("pntt1", "--novel", help="Novel ID"),
) -> None:
    """Run S1 on the gold chapters and write drops and dev scores to a report."""
    config = load_config(novel, configs_dir=Path("configs"))
    gold = load_gold(Path("eval") / novel)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    try:
        init_schema(conn)
        repo = Repository(conn)
        run_s0(
            repo, novel_id=novel, raw_dir=raw_dir, config=config,
            chapter_limit=gold.meta["chapter_max"],
        )
        report = run_gold_chapters(repo, gold=gold, config=config, prompt=prompt, only_dev=only_dev)
    finally:
        conn.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"written={report['written']} failed={report['failed']} report={out}")


if __name__ == "__main__":
    typer.run(main)
