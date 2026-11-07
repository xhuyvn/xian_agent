"""Extractor benchmark (S1-T7): compare local models on the same chapters through real S1."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import json
import multiprocessing
import re
from pathlib import Path
import sqlite3
import statistics
import subprocess
import threading
import time
from typing import Any, Callable

import typer

from xn.config import load_config
from xn.gold import FLAGS, GoldSet, gold_split, load_gold, score_extraction
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
    Candidate("qwen38-ud-q3-10k", "qwen3.8:UD_Q3", think=False),
    Candidate("qwen38-udiq4-hui-10k", "qwen3.8:udiq4_hui", think=False),
    Candidate("qwen25-14b-10k", "qwen2.5:14b-instruct"),
    Candidate("qwen25-14b-16k", "qwen2.5:14b-instruct"),
    Candidate("qwen35-9b-udq6-10k", "qwen3.5:9b_UD_Q6", think=False),
    # G-30: the owner built this model in Ollama (Q8_0, MTP draft 2, num_ctx 12240); no Modelfile here.
    Candidate("qwen3.5_12k:q8_mtp", "qwen3.5_12k:q8_mtp", think=False),
)
GOLD_SPLITS = ("all", "dev", "held_out")


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


def db_stem(serving_name: str) -> str:
    """Return the serving name as a file name stem: characters Windows forbids become '-'."""
    return re.sub(r'[<>:"/\|?*]', "-", serving_name)


def select_candidates(names: list[str] | None) -> tuple[Candidate, ...]:
    """Return the candidates with these serving names, or all of them when names is empty."""
    if not names:
        return CANDIDATES
    known = {candidate.serving_name: candidate for candidate in CANDIDATES}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(f"Unknown models: {unknown}. Known: {sorted(known)}")
    return tuple(known[name] for name in names)


def with_prompt(config: dict[str, Any], version: str, prompts_dir: Path = Path("prompts")) -> dict[str, Any]:
    """Return a copy of config whose S1 extraction prompt is this version; config is not changed.

    Raises:
        FileNotFoundError: prompts_dir has no s1_extract prompt of this version.
    """
    path = Path(prompts_dir) / "s1_extract" / f"{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    cfg = copy.deepcopy(config)
    cfg["prompts"] = {**(cfg.get("prompts") or {}), "s1_extract": version}
    return cfg


def split_chapter_ids(gold: GoldSet, split: str) -> set[int]:
    """Return the chapter ids of one gold split: all, dev or held_out."""
    if split not in GOLD_SPLITS:
        raise ValueError(f"Unknown gold split {split!r}. Known: {list(GOLD_SPLITS)}")
    if split == "all":
        return {c.chapter_id for c in gold.chapters}
    dev, held_out = gold_split(gold.chapters)
    return {c.chapter_id for c in (dev if split == "dev" else held_out)}


def pick_chapters(chapter_max: int, count: int) -> list[int]:
    """Return count chapter numbers evenly spaced across 1..chapter_max, ends included."""
    if count < 2 or count > chapter_max:
        raise ValueError(f"count must be between 2 and {chapter_max}, got {count}")
    return [1 + round(i * (chapter_max - 1) / (count - 1)) for i in range(count)]


def pick_gold_chapters(gold_ids: set[int], chapter_max: int, extra: int) -> list[int]:
    """Return the gold chapters plus extra non-gold chapters evenly spaced across 1..chapter_max.

    This is the G-13 chapter set, so local and cloud runs share chapters.

    Raises:
        ValueError: If fewer than extra non-gold chapters exist.
    """
    others = [n for n in range(1, chapter_max + 1) if n not in gold_ids]
    if extra > len(others):
        raise ValueError(f"Asked for {extra} non-gold chapters, only {len(others)} exist")
    picked = {others[i * len(others) // extra] for i in range(extra)}
    return sorted(gold_ids | picked)


def gold_payloads(repo: Repository, *, novel_id: str, gold: GoldSet) -> dict[int, dict[str, Any]]:
    """Return this database's extractions of the gold chapters."""
    ids = {c.chapter_id for c in gold.chapters}
    return {
        row["chapter_id"]: json.loads(row["payload"])
        for row in repo.list_extractions(novel_id=novel_id, chapter_max=max(ids))
        if row["chapter_id"] in ids
    }


def gold_scores(repo: Repository, *, novel_id: str, gold: GoldSet) -> dict[str, Any]:
    """Score this database's extractions on all gold chapters, then dev and held out apart."""
    return score_gold(gold, gold_payloads(repo, novel_id=novel_id, gold=gold))


def score_gold(gold: GoldSet, payloads: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Score extractions on all gold chapters that ran, then dev and held out apart."""
    dev, held_out = gold_split(gold.chapters)

    def score(chapters: list[Any]) -> dict[str, Any] | None:
        ran = [c for c in chapters if c.chapter_id in payloads]
        return score_extraction(ran, payloads) if ran else None

    return {"all": score(gold.chapters), "dev": score(dev), "held_out": score(held_out)}


def render_gold(scores: dict[str, dict[str, Any]]) -> str:
    """Render gold scores per model as a Markdown table."""
    models = list(scores)

    def cell(model: str, split: str, value: Callable[[dict[str, Any]], str]) -> str:
        found = scores[model][split]
        return "n/a" if found is None else value(found)

    rows: list[tuple[str, str, Callable[[dict[str, Any]], str]]] = [
        ("Entity recall", "all", lambda s: f"{s['recall']:.2f} ({s['found']} of {s['gold_entities']})"),
        ("Entity precision", "all", lambda s: f"{s['precision']:.2f} ({s['false_names']} false names)"),
        ("Recall, dev", "dev", lambda s: f"{s['recall']:.2f}"),
        ("Precision, dev", "dev", lambda s: f"{s['precision']:.2f}"),
        ("Recall, held out", "held_out", lambda s: f"{s['recall']:.2f}"),
        ("Precision, held out", "held_out", lambda s: f"{s['precision']:.2f}"),
        ("Category accuracy", "all", lambda s: f"{s['category_accuracy']:.2f}"),
        ("Event-type accuracy", "all", lambda s: f"{s['event_type_accuracy']:.2f}"),
    ]
    for flag in FLAGS:
        rows.append((f"Flag F1, `{flag}`", "all", lambda s, f=flag: f"{s['flag_f1'][f]:.2f}"))
    lines = [
        "Gold scores, on the gold chapters that ran:",
        "",
        "| Metric | " + " | ".join(models) + " |",
        "|---|" + "---|" * len(models),
    ]
    for label, split, value in rows:
        lines.append(f"| {label} | " + " | ".join(cell(m, split, value) for m in models) + " |")
    return "\n".join(lines) + "\n"


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
    """Return a config copy whose S1 model is the candidate.

    The candidate goes on local_extractor and, when the config has one, on s1_extractor
    (G-27.1), which S1 then runs on.
    """
    cfg = copy.deepcopy(config)
    for key in ("local_extractor", "s1_extractor"):
        extractor = cfg["models"].get(key)
        if not extractor:
            continue
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


def _s1_call_count(conn: sqlite3.Connection, *, novel_id: str, serving_name: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM llm_runs WHERE novel_id = ? AND stage = ? AND model = ?",
        (novel_id, STAGE, serving_name),
    ).fetchone()[0]


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
        ("Idle VRAM (MiB)", lambda r: "n/a" if r.baseline_vram_mib is None else str(r.baseline_vram_mib)),
        ("Peak VRAM (MiB)", lambda r: "n/a" if r.peak_vram_mib is None else str(r.peak_vram_mib)),
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
    chapters: list[int] | None = None,
    gold: GoldSet | None = None,
    gold_split_name: str = "all",
) -> tuple[list[ModelResult], str | None, str]:
    """Run every candidate on the same chapters, each in its own database, and write the report.

    chapters overrides the evenly spaced pick of count chapters. With gold, each model is also
    scored against the gold chapters that ran; every chapter of gold_split_name (all, dev or
    held_out) must be among them.
    Reruns resume: S1 skips chapters already done, so no candidate repeats finished work.

    Raises:
        ValueError: A gold chapter is not among the chapters run.
    """
    novel_id = config["novel_id"]
    chapters = sorted(chapters) if chapters else pick_chapters(chapter_max, count)
    if gold is not None:
        absent = sorted(split_chapter_ids(gold, gold_split_name) - set(chapters))
        if absent:
            raise ValueError(f"Gold chapters not in the run: {absent}")
    out_dir.mkdir(parents=True, exist_ok=True)

    measured: dict[str, dict[str, Any]] = {}
    found_by_model: dict[str, dict[int, set[str]]] = {}
    not_in_text: dict[str, int] = {}
    scored: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        name = candidate.serving_name
        conn = connect(out_dir / f"{db_stem(name)}.db")
        try:
            init_schema(conn)
            repo = Repository(conn)
            run_s0(repo, novel_id=novel_id, raw_dir=raw_dir, config=config, chapter_limit=chapter_max)
            for other in candidates:
                unload(other.serving_name)
            baseline = read_vram()
            calls_before = _s1_call_count(conn, novel_id=novel_id, serving_name=name)
            with _PeakVram(read_vram) as peak:
                run_s1(
                    repo, novel_id=novel_id, config=_candidate_config(config, candidate),
                    chapter_from=chapters[0], chapter_to=chapters[-1], only_chapters=set(chapters),
                    model=models[name] if models else None, prompts_dir=prompts_dir,
                )
            unload(name)
            # A fully resumed run never loads the model, so its readings say nothing about it.
            loaded = _s1_call_count(conn, novel_id=novel_id, serving_name=name) > calls_before
            measured[name] = {
                **_measure_runs(conn, novel_id=novel_id, chapters=chapters, serving_name=name),
                "baseline_vram_mib": baseline if loaded else None,
                "peak_vram_mib": peak.peak if loaded else None,
            }
            found_by_model[name], not_in_text[name] = _found_names(repo, novel_id=novel_id, chapters=chapters)
            if gold is not None:
                scored[name] = gold_scores(repo, novel_id=novel_id, gold=gold)
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
    if gold is not None:
        report += "\n" + render_gold(scored)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(
            {
                "chapters": chapters, "prompt": (config.get("prompts") or {}).get("s1_extract", "v1"),
                "results": [asdict(r) for r in results], "winner": winner,
                "reason": reason, **({"gold": scored} if gold is not None else {}),
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return results, winner, reason


@dataclass(frozen=True)
class ParallelResult:
    """One model run by several worker processes at once, timed by wall clock."""

    serving_name: str
    workers: int
    chapters: int
    schema_success: float
    wall_seconds: float
    wall_s_per_chapter: float
    call_s_mean: float
    call_s_p95: float
    baseline_vram_mib: int | None
    peak_vram_mib: int | None


def run_worker(
    db_path: str, config: dict[str, Any], chapters: list[int], prompts_dir: str, model: Any = None
) -> None:
    """Run S1 on these chapters in one database: the body of one worker process."""
    conn = connect(db_path)
    try:
        run_s1(
            Repository(conn), novel_id=config["novel_id"], config=config,
            chapter_from=chapters[0], chapter_to=chapters[-1], only_chapters=set(chapters),
            model=model, prompts_dir=Path(prompts_dir),
        )
    finally:
        conn.close()


def spawn_workers(jobs: list[tuple[str, dict[str, Any], list[int], str]]) -> None:
    """Start one process per job, wait for all, and raise if any failed."""
    context = multiprocessing.get_context("spawn")
    procs = [context.Process(target=run_worker, args=job) for job in jobs]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join()
    failed = [proc.exitcode for proc in procs if proc.exitcode]
    if failed:
        raise RuntimeError(f"{len(failed)} worker(s) failed, exit codes {failed}")


def _chapter_seconds(
    conn: sqlite3.Connection, *, novel_id: str, chapters: list[int], serving_name: str
) -> list[float]:
    """Return S1 call seconds summed per chapter, retries included."""
    seconds = {n: 0.0 for n in chapters}
    for row in conn.execute(
        "SELECT chapter_id, latency_ms FROM llm_runs WHERE novel_id = ? AND stage = ? AND model = ?",
        (novel_id, STAGE, serving_name),
    ):
        if row["chapter_id"] in seconds:
            seconds[row["chapter_id"]] += row["latency_ms"] / 1000
    return list(seconds.values())


def render_parallel(result: ParallelResult) -> str:
    """Render a parallel run as a Markdown table."""

    def vram(value: int | None) -> str:
        return "n/a" if value is None else str(value)

    rows = [
        ("Workers", str(result.workers)),
        ("Chapters", str(result.chapters)),
        ("Schema success", f"{result.schema_success:.0%}"),
        ("Wall time (s)", f"{result.wall_seconds:.1f}"),
        ("Wall s/chapter", f"{result.wall_s_per_chapter:.1f}"),
        ("Call s/chapter (mean)", f"{result.call_s_mean:.1f}"),
        ("Call s/chapter (p95)", f"{result.call_s_p95:.1f}"),
        ("Idle VRAM (MiB)", vram(result.baseline_vram_mib)),
        ("Peak VRAM (MiB)", vram(result.peak_vram_mib)),
    ]
    lines = [f"| Metric | {result.serving_name} |", "|---|---|"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    return "\n".join(lines) + "\n"


def run_parallel(
    *,
    config: dict[str, Any],
    raw_dir: Path,
    out_dir: Path,
    chapter_max: int,
    candidate: Candidate,
    chapters: list[int],
    workers: int,
    gold: GoldSet | None = None,
    prompts_dir: Path = Path("prompts"),
    read_vram: Callable[[], int | None] = read_vram_mib,
    unload: Callable[[str], None] = stop_model,
    spawn: Callable[[list[tuple[str, dict[str, Any], list[int], str]]], None] = spawn_workers,
) -> tuple[ParallelResult, dict[str, Any] | None]:
    """Run one model on the chapters with workers processes at once, each on its own database.

    Chapters are dealt round-robin, so each worker gets a spread of chapter lengths. Wall
    s/chapter is wall time over chapters, which is what parallel requests can speed up.

    Raises:
        ValueError: workers is below 2, or a worker database already holds S1 calls of this
            model, because resumed chapters would make the wall time say nothing.
    """
    if workers < 2:
        raise ValueError(f"workers must be at least 2, got {workers}")
    novel_id = config["novel_id"]
    name = candidate.serving_name
    chapters = sorted(chapters)
    shares = [chapters[i::workers] for i in range(workers)]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / f"{db_stem(name)}-w{workers}-{i + 1}.db" for i in range(workers)]
    for path in paths:
        conn = connect(path)
        try:
            init_schema(conn)
            if _s1_call_count(conn, novel_id=novel_id, serving_name=name):
                raise ValueError(f"{path} already has S1 calls for {name}; parallel runs need fresh databases")
            run_s0(Repository(conn), novel_id=novel_id, raw_dir=raw_dir, config=config, chapter_limit=chapter_max)
        finally:
            conn.close()

    cfg = _candidate_config(config, candidate)
    unload(name)
    baseline = read_vram()
    started = time.perf_counter()
    with _PeakVram(read_vram) as peak:
        spawn([(str(path), cfg, share, str(prompts_dir)) for path, share in zip(paths, shares)])
    wall = time.perf_counter() - started
    unload(name)

    done = 0
    seconds: list[float] = []
    payloads: dict[int, dict[str, Any]] = {}
    for path, share in zip(paths, shares):
        conn = connect(path)
        try:
            measured = _measure_runs(conn, novel_id=novel_id, chapters=share, serving_name=name)
            done += round(measured["schema_success"] * len(share))
            seconds += _chapter_seconds(conn, novel_id=novel_id, chapters=share, serving_name=name)
            if gold is not None:
                payloads |= gold_payloads(Repository(conn), novel_id=novel_id, gold=gold)
        finally:
            conn.close()
    ordered = sorted(seconds)
    result = ParallelResult(
        serving_name=name, workers=workers, chapters=len(chapters), schema_success=done / len(chapters),
        wall_seconds=wall, wall_s_per_chapter=wall / len(chapters), call_s_mean=statistics.fmean(ordered),
        call_s_p95=ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))],
        baseline_vram_mib=baseline, peak_vram_mib=peak.peak,
    )
    scores = score_gold(gold, payloads) if gold is not None else None
    stem = out_dir / f"parallel-{db_stem(name)}-w{workers}"
    report = render_parallel(result)
    if scores is not None:
        report += "\n" + render_gold({name: scores})
    stem.with_suffix(".md").write_text(report, encoding="utf-8")
    stem.with_suffix(".json").write_text(
        json.dumps(
            {"chapters": chapters, "shares": shares, "result": asdict(result), "gold": scores},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return result, scores


def main(
    raw_dir: Path = typer.Option(..., "--raw-dir", help="Path to raw chapters directory"),
    out_dir: Path = typer.Option(..., "--out", help="Directory for benchmark databases and reports"),
    chapter_max: int = typer.Option(1121, "--chapter-max", help="Last chapter S0 reads"),
    count: int = typer.Option(50, "--count", help="Chapters to benchmark"),
    novel: str = typer.Option("pntt1", "--novel", help="Novel ID"),
    model: list[str] | None = typer.Option(None, "--model", help="Serving name to run; repeat for more (default: all)"),
    gold_set: bool = typer.Option(
        False, "--gold", help="Run the gold chapters plus --extra-chapters others and score them; ignores --count",
    ),
    extra_chapters: int = typer.Option(30, "--extra-chapters", help="Non-gold chapters to add with --gold"),
    workers: int = typer.Option(1, "--workers", help="With one --model: S1 processes to run at once"),
    prompt: str | None = typer.Option(
        None, "--prompt", help="S1 extraction prompt version, e.g. v3, set on a copy of the config (default: config's)",
    ),
    gold_split_name: str = typer.Option(
        "all", "--gold-split",
        help="With --gold: all runs the gold chapters plus --extra-chapters others; dev or held_out runs only that split",
    ),
) -> None:
    """Benchmark the extractor candidates and print the report."""
    config = load_config(novel, configs_dir=Path("configs"))
    try:
        candidates = select_candidates(model)
        if prompt is not None:
            config = with_prompt(config, prompt)
    except (ValueError, FileNotFoundError) as err:
        raise typer.BadParameter(str(err))
    if gold_split_name not in GOLD_SPLITS:
        raise typer.BadParameter(f"--gold-split must be one of {list(GOLD_SPLITS)}, got {gold_split_name}")
    if gold_split_name != "all" and not gold_set:
        raise typer.BadParameter("--gold-split needs --gold")
    gold = load_gold(Path("eval") / novel) if gold_set else None
    chapters = None
    if gold is not None:
        split_ids = split_chapter_ids(gold, gold_split_name)
        chapters = (
            pick_gold_chapters(split_ids, chapter_max, extra_chapters) if gold_split_name == "all" else sorted(split_ids)
        )
    if workers > 1:
        if len(candidates) != 1:
            raise typer.BadParameter("--workers needs exactly one --model")
        result, _ = run_parallel(
            config=config, raw_dir=raw_dir, out_dir=out_dir, chapter_max=chapter_max, candidate=candidates[0],
            chapters=chapters or pick_chapters(chapter_max, count), workers=workers, gold=gold,
        )
        stem = out_dir / f"parallel-{db_stem(result.serving_name)}-w{workers}"
        typer.echo(stem.with_suffix(".md").read_text(encoding="utf-8"))
        return
    run_benchmark(
        config=config, raw_dir=raw_dir, out_dir=out_dir, chapter_max=chapter_max, count=count,
        candidates=candidates, chapters=chapters, gold=gold, gold_split_name=gold_split_name,
    )
    typer.echo((out_dir / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    typer.run(main)
