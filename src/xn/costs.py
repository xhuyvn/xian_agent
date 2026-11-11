r"""Cost and time of an ingest run, per stage and model (G-33).

Built from what the database already holds, so nothing is duplicated:
- `pipeline_runs` gives the run's novel, chapter range and time window, and the stage times it recorded;
- `llm_runs` gives each call's stage, model, tokens, latency, retries, success and cost;
- `jobs` gives when each chapter, arc or checkpoint of a stage was last updated;
- `chapters` gives the source size (characters of cleaned text).

`llm_runs` has no pipeline run id, so a run's calls are the novel's calls inside the run's window.
Wall time per stage is the sum of the gaps between its successive events (calls and job updates),
leaving out gaps longer than `gap_s`, which are pauses, or the time `pipeline_runs` recorded for it
when that is longer. Calls made before the stage's last pass
started (its first job update, less `gap_s`) were redone later; they count in the run's spend but
not in the rates used for forecasts.

The report never writes to the database: `open_readonly` opens it read-only, so it is safe on a
database a live run is writing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

DEFAULT_GAP_S = 600.0  # a quiet spell longer than this inside a stage is a pause, not work
STAGE_ORDER = ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "prefix", "s7")
COUNTS = ("calls", "failed", "retries", "input_tokens", "output_tokens", "usd", "unknown_cost", "call_s")


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    r"""Open a SQLite database read-only (works while another process writes it in WAL mode)."""
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ts(text: str | None) -> float | None:
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def active_seconds(times: list[float], gap_s: float = DEFAULT_GAP_S) -> float:
    r"""Sum of the gaps between successive events, leaving out gaps longer than gap_s."""
    ordered = sorted(times)
    return float(sum(b - a for a, b in zip(ordered, ordered[1:]) if b - a <= gap_s))


def _blank() -> dict[str, Any]:
    return {k: 0 for k in COUNTS} | {"usd": 0.0, "call_s": 0.0}


def _add(acc: dict[str, Any], row: sqlite3.Row) -> None:
    acc["calls"] += 1
    acc["failed"] += 0 if row["ok"] else 1
    acc["retries"] += row["retries"] or 0
    acc["input_tokens"] += row["input_tokens"] or 0
    acc["output_tokens"] += row["output_tokens"] or 0
    if row["cost_usd"] is None:
        acc["unknown_cost"] += 1
    else:
        acc["usd"] += row["cost_usd"]
    acc["call_s"] += (row["latency_ms"] or 0) / 1000.0


def _rates(acc: dict[str, Any], chapters: int, chars: int) -> dict[str, dict[str, float]]:
    keys = ("calls", "input_tokens", "output_tokens", "usd", "wall_s")
    per_ch = {k: acc.get(k, 0) / chapters for k in keys} if chapters else {}
    per_k = {k: acc.get(k, 0) / (chars / 1000.0) for k in keys} if chars else {}
    return {"per_chapter": per_ch, "per_1k_chars": per_k}


def run_costs(conn: sqlite3.Connection, run_id: str, *, gap_s: float = DEFAULT_GAP_S,
              now: str | None = None) -> dict[str, Any]:
    r"""The cost and time summary of one pipeline run, as a JSON-ready dict."""
    run = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"no pipeline run {run_id}")
    novel_id = run["novel_id"]
    start = run["started_at"]
    end = run["finished_at"] or now or datetime.now(timezone.utc).isoformat()
    chapter_to = run["chapter_to"]
    size = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(clean_text)), 0), COALESCE(SUM(token_count), 0) FROM chapters "
        "WHERE novel_id = ? AND (? IS NULL OR chapter_id <= ?)", (novel_id, chapter_to, chapter_to),
    ).fetchone()
    chapters, chars, text_tokens = int(size[0]), int(size[1]), int(size[2])

    job_times: dict[str, list[float]] = defaultdict(list)
    job_units: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in conn.execute(
        "SELECT stage, status, updated_at FROM jobs WHERE novel_id = ? AND updated_at >= ? AND updated_at <= ?",
        (novel_id, start, end),
    ):
        t = _ts(row["updated_at"])
        if t is not None:
            job_times[row["stage"]].append(t)
        job_units[row["stage"]][row["status"]] += 1

    calls = conn.execute(
        "SELECT stage, model, ok, retries, input_tokens, output_tokens, cost_usd, latency_ms, created_at "
        "FROM llm_runs WHERE novel_id = ? AND created_at >= ? AND created_at <= ? ORDER BY created_at",
        (novel_id, start, end),
    ).fetchall()

    recorded = json.loads(run["stage_times"] or "{}")
    stages_seen = {r["stage"] for r in calls} | set(job_times)
    stage_list = [s for s in STAGE_ORDER if s in stages_seen] + sorted(stages_seen - set(STAGE_ORDER))
    stages = []
    totals = _blank() | {"wall_s": 0.0}
    redone_total = _blank()
    for stage in stage_list:
        first_job = min(job_times[stage]) if job_times[stage] else None
        cutoff = first_job - gap_s if first_job is not None else None
        models: dict[str, dict[str, Any]] = defaultdict(_blank)
        redone: dict[str, dict[str, Any]] = defaultdict(_blank)
        times = list(job_times[stage])
        for row in (r for r in calls if r["stage"] == stage):
            t = _ts(row["created_at"])
            if cutoff is not None and t is not None and t < cutoff:
                _add(redone[row["model"]], row)
                _add(redone_total, row)
                continue
            _add(models[row["model"]], row)
            if t is not None:
                times.append(t)
        # The stage time pipeline_runs recorded covers only the part run inside one `xn ingest`
        # process, so it is a floor: it wins for stages too short to leave a trail of events.
        wall = max(active_seconds(times, gap_s), float(recorded.get(stage) or 0.0))
        acc = _blank()
        for m in models.values():
            for k in COUNTS:
                acc[k] += m[k]
        acc["wall_s"] = wall
        model_rows = []
        for name, m in sorted(models.items(), key=lambda kv: -kv[1]["calls"]):
            share = m["call_s"] / acc["call_s"] if acc["call_s"] else 0.0
            m = dict(m, model=name, wall_s=wall * share)
            ok = m["calls"] - m["failed"]  # failed calls carry no tokens
            m["per_call"] = {
                "input_tokens": m["input_tokens"] / ok, "output_tokens": m["output_tokens"] / ok,
                "seconds": m["call_s"] / m["calls"],
            } if ok else {}
            m.update(_rates(m, chapters, chars))
            model_rows.append(m)
        units = dict(job_units[stage])
        live = run["finished_at"] is None and run["current_stage"] == stage
        partial = live or bool(units.get("running") or units.get("pending"))
        entry = dict(acc, stage=stage, recorded_s=recorded.get(stage), units=units, partial=partial,
                     models=model_rows,
                     redone=[dict(v, model=k) for k, v in sorted(redone.items())])
        entry.update(_rates(acc, chapters, chars))
        stages.append(entry)
        for k in COUNTS:
            totals[k] += acc[k]
        totals["wall_s"] += wall
    totals.update(_rates(totals, chapters, chars))
    return {
        "run_id": run_id, "novel_id": novel_id, "status": run["status"], "started_at": start,
        "ended_at": end, "finished": run["finished_at"] is not None, "chapter_to": chapter_to,
        "chapters": chapters, "chars": chars, "text_tokens": text_tokens, "gap_s": gap_s,
        "generated_at": datetime.now(timezone.utc).isoformat(), "stages": stages, "totals": totals,
        "redone": redone_total,
    }


def write_costs(summary: dict[str, Any], out_dir: str | Path) -> Path:
    r"""Write the summary as `<out_dir>/<run_id>-costs.json` and return the path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{summary['run_id']}-costs.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    return f"{seconds / 3600:.1f}h" if seconds >= 5400 else f"{seconds / 60:.1f}m"


def _k(n: float) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else f"{n:.0f}"


def format_costs(summary: dict[str, Any]) -> str:
    r"""A plain-text table of the summary."""
    lines = [
        f"run {summary['run_id']} ({summary['novel_id']}, {summary['status']}): {summary['chapters']} chapters, "
        f"{summary['chars'] / 1e6:.2f}M chars, {summary['started_at'][:16]} to {summary['ended_at'][:16]}",
        f"{'stage':<7} {'model':<34} {'wall':>7} {'call':>7} {'calls':>6} {'fail':>5} {'retry':>5} "
        f"{'in':>7} {'out':>7} {'usd':>8} {'tok/ch':>7}",
    ]
    for st in summary["stages"]:
        tok_ch = (st["input_tokens"] + st["output_tokens"]) / summary["chapters"] if summary["chapters"] else 0
        lines.append(
            f"{st['stage']:<7} {'(stage)':<34} {_dur(st['wall_s']):>7} {_dur(st['call_s']):>7} {st['calls']:>6} "
            f"{st['failed']:>5} {st['retries']:>5} {_k(st['input_tokens']):>7} {_k(st['output_tokens']):>7} "
            f"{st['usd']:>8.4f} {tok_ch:>7.0f}"
            + (f"  recorded {_dur(st['recorded_s'])}" if st.get("recorded_s") is not None else "")
            + ("  PARTIAL (stage not finished)" if st.get("partial") else "")
        )
        for m in st["models"]:
            per = m.get("per_call") or {}
            lines.append(
                f"{'':<7} {m['model'][:34]:<34} {_dur(m['wall_s']):>7} {_dur(m['call_s']):>7} {m['calls']:>6} "
                f"{m['failed']:>5} {m['retries']:>5} {_k(m['input_tokens']):>7} {_k(m['output_tokens']):>7} "
                f"{m['usd']:>8.4f}  {per.get('seconds', 0):.1f}s/call "
                f"{per.get('input_tokens', 0) + per.get('output_tokens', 0):.0f} tok/call"
            )
        for m in st["redone"]:
            lines.append(f"{'':<7} redone earlier: {m['model']} {m['calls']} calls, "
                         f"{_k(m['input_tokens'] + m['output_tokens'])} tokens, ${m['usd']:.4f}")
    t = summary["totals"]
    lines.append(
        f"total: wall {_dur(t['wall_s'])}, {t['calls']} calls, {t['failed']} failed, {t['retries']} retries, "
        f"{_k(t['input_tokens'])} in, {_k(t['output_tokens'])} out, ${t['usd']:.4f}"
        + (f" ({t['unknown_cost']} calls with no cost)" if t["unknown_cost"] else "")
    )
    r = summary["redone"]
    if r["calls"]:
        lines.append(f"redone work (not in the rates): {r['calls']} calls, "
                     f"{_k(r['input_tokens'] + r['output_tokens'])} tokens, ${r['usd']:.4f}")
    return "\n".join(lines)
