"""Extractor benchmark (S1-T7): compare local models on the same chapters through real S1."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import threading
import time
from typing import Any, Callable

import typer

from xn.config import load_config
from xn.pipeline.s0_parse import run_s0
from xn.pipeline.s1_extract import STAGE, run_s1
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import normalize_alias, strip_diacritics

MIN_SCHEMA_SUCCESS = 0.95
RECALL_TIE_MARGIN = 0.02
VRAM_POLL_SECONDS = 1.0
STOP_WAIT_SECONDS = 30


@dataclass(frozen=True)
class Candidate:
    """One local model setup under test."""

    serving_name: str
    base_name: str
    output_mode: str = "native"
    think: bool | None = None


CANDIDATES = (
    Candidate("qwen35-q8-12k", "qwen3.5:Q8", think=False),
    Candidate("deepseek-r1-14b-12k", "deepseek-r1:14b"),
)


@dataclass(frozen=True)
class ModelResult:
    """Measured results for one candidate."""

    serving_name: str
    chapters: int
    schema_success: float
    first_try_rate: float
    recall: float
    names_in_text: int
    names_not_in_text: int
    s_per_chapter: float
    p95_s_per_chapter: float
    baseline_vram_mib: int | None
    peak_vram_mib: int | None
    chars_per_token: float | None


def pick_chapters(chapter_max: int, count: int) -> list[int]:
    """Return count chapter numbers evenly spaced across 1..chapter_max, ends included."""
    if count < 2 or count > chapter_max:
        raise ValueError(f"count must be between 2 and {chapter_max}, got {count}")
    return [1 + round(i * (chapter_max - 1) / (count - 1)) for i in range(count)]


def read_vram_mib() -> int | None:
    """Return GPU memory in use in MiB, or None if nvidia-smi cannot report it."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True,
        ).stdout
        return int(out.split()[0])
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return None


def stop_model(serving_name: str) -> None:
    """Unload a model and wait for Ollama to release it, so VRAM readings start clean."""
    subprocess.run(["ollama", "stop", serving_name], capture_output=True, check=False)
    for _ in range(STOP_WAIT_SECONDS):
        loaded = subprocess.run(["ollama", "ps"], capture_output=True, text=True, check=False).stdout
        if serving_name not in loaded:
            return
        time.sleep(1)


class _PeakVram:
    """Poll read_vram in a thread and keep the highest reading."""

    def __init__(self, read_vram: Callable[[], int | None]) -> None:
        self._read_vram = read_vram
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self.peak: int | None = None

    def _poll(self) -> None:
        while True:
            value = self._read_vram()
            if value is not None and (self.peak is None or value > self.peak):
                self.peak = value
            if self._done.wait(VRAM_POLL_SECONDS):
                return

    def __enter__(self) -> "_PeakVram":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._done.set()
        self._thread.join()


def _candidate_config(config: dict[str, Any], candidate: Candidate) -> dict[str, Any]:
    cfg = copy.deepcopy(config)
    extractor = cfg["models"]["local_extractor"]
    extractor["name"] = candidate.base_name
    extractor["serving_name"] = candidate.serving_name
    extractor["output_mode"] = candidate.output_mode
    if candidate.think is not None:
        extractor["think"] = candidate.think
    return cfg


def _name_key(name: str) -> str:
    return strip_diacritics(normalize_alias(name))


def _found_names(
    repo: Repository, *, novel_id: str, chapters: list[int]
) -> tuple[dict[int, set[str]], int]:
    """Return per-chapter names that appear in the chapter text, and the count that do not."""
    last = chapters[-1]
    texts = {
        ch["chapter_id"]: _name_key(ch["clean_text"])
        for ch in repo.list_chapters(novel_id=novel_id, chapter_max=last)
        if ch["chapter_id"] in chapters
    }
    found: dict[int, set[str]] = {n: set() for n in chapters}
    not_in_text = 0
    for row in repo.list_extractions(novel_id=novel_id, chapter_max=last):
        chapter_id = row["chapter_id"]
        if chapter_id not in found:
            continue
        keys = {_name_key(ev["name"]) for ev in json.loads(row["payload"])["events"]}
        keys.discard("")
        verified = {key for key in keys if key in texts[chapter_id]}
        found[chapter_id] |= verified
        not_in_text += len(keys - verified)
    return found, not_in_text


def pooled_recall(found_by_model: dict[str, dict[int, set[str]]]) -> dict[str, float]:
    """Score each model against the union of all models' names, chapter by chapter."""
    pool: dict[int, set[str]] = {}
    for found in found_by_model.values():
        for chapter_id, names in found.items():
            pool.setdefault(chapter_id, set()).update(names)
    total = sum(len(names) for names in pool.values())
    return {
        model: sum(len(names) for names in found.values()) / total if total else 0.0
        for model, found in found_by_model.items()
    }


def _measure_runs(
    conn: sqlite3.Connection, *, novel_id: str, chapters: list[int], serving_name: str
) -> dict[str, Any]:
    """Read schema success, timing and token data from jobs and llm_runs."""
    chapter_set = set(chapters)
    done = sum(
        1
        for row in conn.execute(
            "SELECT chapter_id FROM jobs WHERE novel_id = ? AND stage = ? AND status = 'done'",
            (novel_id, STAGE),
        )
        if row["chapter_id"] in chapter_set
    )
    runs = [
        row
        for row in conn.execute(
            "SELECT chapter_id, latency_ms, ok, retries, input_tokens FROM llm_runs "
            "WHERE novel_id = ? AND stage = ? AND model = ?",
            (novel_id, STAGE, serving_name),
        )
        if row["chapter_id"] in chapter_set
    ]
    seconds = {n: 0.0 for n in chapters}
    calls = {n: 0 for n in chapters}
    for row in runs:
        seconds[row["chapter_id"]] += row["latency_ms"] / 1000
        calls[row["chapter_id"]] += 1

    # Only whole-chapter calls with no retry have one chapter's text in input_tokens.
    first_try = [
        row for row in runs if row["ok"] == 1 and row["retries"] == 0 and calls[row["chapter_id"]] == 1
    ]
    text_len = {
        row["chapter_id"]: row["n"]
        for row in conn.execute(
            "SELECT chapter_id, length(clean_text) AS n FROM chapters WHERE novel_id = ?", (novel_id,)
        )
        if row["chapter_id"] in chapter_set
    }
    chars_per_token = None
    points = [(text_len[row["chapter_id"]], row["input_tokens"]) for row in first_try]
    if len({x for x, _ in points}) >= 2:
        slope = statistics.linear_regression([x for x, _ in points], [y for _, y in points]).slope
        chars_per_token = 1 / slope if slope > 0 else None

    ordered = sorted(seconds.values())
    return {
        "schema_success": done / len(chapters),
        "first_try_rate": len(first_try) / len(chapters),
        "s_per_chapter": statistics.fmean(ordered),
        "p95_s_per_chapter": ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))],
        "chars_per_token": chars_per_token,
    }


def choose_winner(results: list[ModelResult]) -> tuple[str | None, str]:
    """Apply the agreed rule: schema success gate, then recall, then speed within the margin."""
    eligible = [r for r in results if r.schema_success >= MIN_SCHEMA_SUCCESS]
    if not eligible:
        return None, f"No model reached {MIN_SCHEMA_SUCCESS:.0%} schema success."
    best_recall = max(r.recall for r in eligible)
    close = [r for r in eligible if round(best_recall - r.recall, 9) <= RECALL_TIE_MARGIN]
    winner = min(close, key=lambda r: r.s_per_chapter)
    if len(close) > 1:
        return winner.serving_name, "Recall within 2 points, so the faster model wins."
    return winner.serving_name, "Highest recall among models that passed the schema gate."


def render_report(results: list[ModelResult], chapters: list[int], winner: str | None, reason: str) -> str:
    """Render the results as a Markdown table with the outcome of the winner rule."""
    columns = [
        ("Schema success", lambda r: f"{r.schema_success:.0%}"),
        ("First-try success", lambda r: f"{r.first_try_rate:.0%}"),
        ("Pooled recall", lambda r: f"{r.recall:.2f}"),
        ("Names in text", lambda r: str(r.names_in_text)),
        ("Names not in text", lambda r: str(r.names_not_in_text)),
        ("s/chapter (mean)", lambda r: f"{r.s_per_chapter:.1f}"),
        ("s/chapter (p95)", lambda r: f"{r.p95_s_per_chapter:.1f}"),
        ("Idle VRAM (MiB)", lambda r: str(r.baseline_vram_mib)),
        ("Peak VRAM (MiB)", lambda r: str(r.peak_vram_mib)),
        ("Chars per token", lambda r: "n/a" if r.chars_per_token is None else f"{r.chars_per_token:.2f}"),
    ]
    lines = [
        f"Chapters: {len(chapters)}, from {chapters[0]} to {chapters[-1]}.",
        "",
        "| Metric | " + " | ".join(r.serving_name for r in results) + " |",
        "|---|" + "---|" * len(results),
    ]
    for label, cell in columns:
        lines.append(f"| {label} | " + " | ".join(cell(r) for r in results) + " |")
    lines += ["", f"Winner: {winner or 'none'}. {reason}"]
    return "\n".join(lines) + "\n"


def run_benchmark(
    *,
    config: dict[str, Any],
    raw_dir: Path,
    out_dir: Path,
    chapter_max: int,
    count: int,
    candidates: tuple[Candidate, ...] = CANDIDATES,
    models: dict[str, Any] | None = None,
    prompts_dir: Path = Path("prompts"),
    read_vram: Callable[[], int | None] = read_vram_mib,
    unload: Callable[[str], None] = stop_model,
) -> tuple[list[ModelResult], str | None, str]:
    """Run every candidate on the same chapters, each in its own database, and write the report.

    Reruns resume: S1 skips chapters already done, so no candidate repeats finished work.
    """
    novel_id = config["novel_id"]
    chapters = pick_chapters(chapter_max, count)
    out_dir.mkdir(parents=True, exist_ok=True)

    measured: dict[str, dict[str, Any]] = {}
    found_by_model: dict[str, dict[int, set[str]]] = {}
    not_in_text: dict[str, int] = {}
    for candidate in candidates:
        name = candidate.serving_name
        conn = connect(out_dir / f"{name}.db")
        try:
            init_schema(conn)
            repo = Repository(conn)
            run_s0(repo, novel_id=novel_id, raw_dir=raw_dir, config=config, chapter_limit=chapter_max)
            for other in candidates:
                unload(other.serving_name)
            baseline = read_vram()
            with _PeakVram(read_vram) as peak:
                run_s1(
                    repo, novel_id=novel_id, config=_candidate_config(config, candidate),
                    chapter_from=chapters[0], chapter_to=chapters[-1], only_chapters=set(chapters),
                    model=models[name] if models else None, prompts_dir=prompts_dir,
                )
            unload(name)
            measured[name] = {
                **_measure_runs(conn, novel_id=novel_id, chapters=chapters, serving_name=name),
                "baseline_vram_mib": baseline,
                "peak_vram_mib": peak.peak,
            }
            found_by_model[name], not_in_text[name] = _found_names(repo, novel_id=novel_id, chapters=chapters)
        finally:
            conn.close()

    recall = pooled_recall(found_by_model)
    results = [
        ModelResult(
            serving_name=name, chapters=len(chapters), recall=recall[name],
            names_in_text=sum(len(names) for names in found_by_model[name].values()),
            names_not_in_text=not_in_text[name], **values,
        )
        for name, values in measured.items()
    ]
    winner, reason = choose_winner(results)
    report = render_report(results, chapters, winner, reason)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(
            {"chapters": chapters, "results": [asdict(r) for r in results], "winner": winner, "reason": reason},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return results, winner, reason


def main(
    raw_dir: Path = typer.Option(..., "--raw-dir", help="Path to raw chapters directory"),
    out_dir: Path = typer.Option(..., "--out", help="Directory for benchmark databases and reports"),
    chapter_max: int = typer.Option(1121, "--chapter-max", help="Last chapter S0 reads"),
    count: int = typer.Option(50, "--count", help="Chapters to benchmark"),
    novel: str = typer.Option("pntt1", "--novel", help="Novel ID"),
) -> None:
    """Benchmark the extractor candidates and print the report."""
    config = load_config(novel, configs_dir=Path("configs"))
    results, winner, reason = run_benchmark(
        config=config, raw_dir=raw_dir, out_dir=out_dir, chapter_max=chapter_max, count=count,
    )
    typer.echo(render_report(results, pick_chapters(chapter_max, count), winner, reason))


if __name__ == "__main__":
    typer.run(main)
