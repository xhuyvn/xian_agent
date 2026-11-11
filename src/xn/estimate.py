r"""Forecast the time, tokens and cost of an ingest before it starts (G-33).

A forecast scales a measured run (its `xn costs` summary) to a new size: by chapter count, or by
characters of cleaned text when a raw directory is given. Most stages scale linearly. Stages that
run on a free-quota model pool (`stage_pools`) are simulated instead: their calls go to the pool's
models in order, each model taking calls until the quota it has left runs out, with each model's
measured tokens and seconds per call. A model never measured on that stage gets the stage's
non-thinking rate, times the thinking factor if its pool entry turns thinking on; the factor is
measured where a stage ran both kinds, else defaults apply. Pools are shared across stages in
stage order. Calls no model can take are reported as unserved: the run would stop there.

Quota left per pool model = its quota (`quota_tokens` on the pool entry or the pool, default 1M)
minus the input and output tokens recorded for it in the given databases; a model marked used up
in `model_quota` has 0 left. Quota spent in databases that are not given is not seen.
"""

from __future__ import annotations

import math
from pathlib import Path
import re
import sqlite3
from typing import Any

from xn.costs import STAGE_ORDER

DEFAULT_QUOTA_TOKENS = 1_000_000
DEFAULT_THINKING_TOKENS = 7.0  # per-call token factor of a thinking model, when not measured
DEFAULT_THINKING_SECONDS = 20.0  # per-call time factor, when not measured
CHAPTER_FILE = re.compile(r"^chapter_(\d+)\.md$")


def raw_dir_size(raw_dir: str | Path) -> tuple[int, int]:
    r"""Chapters and characters of cleaned text in a raw directory of `chapter_N.md` files."""
    from xn.pipeline.s0_parse import _markdown_to_plain_text

    chapters = chars = 0
    for entry in sorted(Path(raw_dir).iterdir()):
        if CHAPTER_FILE.match(entry.name):
            chapters += 1
            text = entry.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            chars += len(_markdown_to_plain_text(text))
    if not chapters:
        raise ValueError(f"no chapter_N.md files in {raw_dir}")
    return chapters, chars


def pool_members(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    r"""Each pool's members in order: name, provider, quota and whether thinking is on."""
    pools: dict[str, list[dict[str, Any]]] = {}
    for pool, spec in (config.get("model_pools") or {}).items():
        base_extra = spec.get("extra_body") or {}
        members = []
        for entry in spec.get("models") or []:
            is_dict = isinstance(entry, dict)
            extra = {**base_extra, **((entry.get("extra_body") or {}) if is_dict else {})}
            quota = (entry.get("quota_tokens") if is_dict else None) or spec.get("quota_tokens") or DEFAULT_QUOTA_TOKENS
            members.append({
                "model": str(entry["name"] if is_dict else entry), "provider": str(spec.get("provider") or pool),
                "quota": int(quota), "thinking": bool(extra.get("enable_thinking")),
            })
        pools[str(pool)] = members
    return pools


def quota_left(config: dict[str, Any], conns: list[sqlite3.Connection]) -> dict[str, dict[str, Any]]:
    r"""Per pool model: quota, tokens used in the given databases, whether used up, and tokens left."""
    out: dict[str, dict[str, Any]] = {}
    for pool, members in pool_members(config).items():
        for m in members:
            used, exhausted = 0, False
            for conn in conns:
                row = conn.execute(
                    "SELECT COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)), 0) "
                    "FROM llm_runs WHERE model = ?", (m["model"],),
                ).fetchone()
                used += int(row[0])
                try:
                    hit = conn.execute("SELECT 1 FROM model_quota WHERE provider = ? AND model = ?",
                                       (m["provider"], m["model"])).fetchone()
                except sqlite3.OperationalError:  # an older database without the table
                    hit = None
                exhausted = exhausted or hit is not None
            left = 0 if exhausted else max(m["quota"] - used, 0)
            out[m["model"]] = dict(m, pool=pool, used=used, exhausted=exhausted, left=left)
    return out


def thinking_factors(summary: dict[str, Any], thinking: set[str]) -> tuple[float, float]:
    r"""Token and time factors of thinking over non-thinking models, from stages that ran both."""
    tok, sec = [], []
    for st in summary["stages"]:
        rates = {m["model"]: m["per_call"] for m in st["models"] if m.get("per_call")}
        think = [r for name, r in rates.items() if name in thinking]
        plain = [r for name, r in rates.items() if name not in thinking]
        if think and plain:
            t_tok = sum(r["input_tokens"] + r["output_tokens"] for r in think) / len(think)
            p_tok = sum(r["input_tokens"] + r["output_tokens"] for r in plain) / len(plain)
            t_sec = sum(r["seconds"] for r in think) / len(think)
            p_sec = sum(r["seconds"] for r in plain) / len(plain)
            if p_tok and p_sec:
                tok.append(t_tok / p_tok)
                sec.append(t_sec / p_sec)
    if not tok:
        return DEFAULT_THINKING_TOKENS, DEFAULT_THINKING_SECONDS
    return sum(tok) / len(tok), sum(sec) / len(sec)


def _member_rate(stage: dict[str, Any], member: dict[str, Any], factors: tuple[float, float],
                 thinking: set[str]) -> dict[str, float]:
    measured = {m["model"]: m["per_call"] for m in stage["models"] if m.get("per_call")}
    if member["model"] in measured:
        return dict(measured[member["model"]], measured=1.0)
    plain = [r for name, r in measured.items() if name not in thinking] or list(measured.values())
    base = {k: sum(r[k] for r in plain) / len(plain) for k in ("input_tokens", "output_tokens", "seconds")}
    if member["thinking"]:
        base = {"input_tokens": base["input_tokens"] * factors[0], "output_tokens": base["output_tokens"] * factors[0],
                "seconds": base["seconds"] * factors[1]}
    return dict(base, measured=0.0)


def estimate(summary: dict[str, Any], config: dict[str, Any], quota: dict[str, dict[str, Any]], *,
             chapters: int, chars: int | None = None) -> dict[str, Any]:
    r"""Project each stage of the summary's run onto `chapters` (and `chars`, when known)."""
    if chars is not None and summary["chars"]:
        scale, basis = chars / summary["chars"], "chars"
    else:
        scale, basis = chapters / summary["chapters"], "chapters"
    stage_pools = {str(k): str(v) for k, v in (config.get("stage_pools") or {}).items()}
    pools = pool_members(config)
    thinking = {m["model"] for ms in pools.values() for m in ms if m["thinking"]}
    factors = thinking_factors(summary, thinking)
    left = {name: q["left"] for name, q in quota.items()}
    order = {s: i for i, s in enumerate(STAGE_ORDER)}
    stages_out = []
    for st in sorted(summary["stages"], key=lambda s: order.get(s["stage"], len(order))):
        pool = stage_pools.get(st["stage"])
        if pool in pools and st["calls"] and any(m.get("per_call") for m in st["models"]):
            calls = math.ceil(st["calls"] * scale)
            overhead = max(st["wall_s"] - st["call_s"], 0.0) * scale
            remaining, rows, seconds = calls, [], overhead
            for member in pools[pool]:
                if remaining <= 0:
                    break
                rate = _member_rate(st, member, factors, thinking)
                per_tok = rate["input_tokens"] + rate["output_tokens"]
                can = remaining if per_tok <= 0 else int(left.get(member["model"], 0) // per_tok)
                take = min(remaining, can)
                if take <= 0:
                    continue
                left[member["model"]] = left.get(member["model"], 0) - take * per_tok
                remaining -= take
                seconds += take * rate["seconds"]
                rows.append({"model": member["model"], "calls": take, "input_tokens": take * rate["input_tokens"],
                             "output_tokens": take * rate["output_tokens"], "usd": 0.0,
                             "seconds": take * rate["seconds"], "rate_measured": bool(rate["measured"])})
            stages_out.append({"stage": st["stage"], "pool": pool, "seconds": seconds, "calls": calls,
                               "partial": bool(st.get("partial")),
                               "unserved_calls": remaining, "usd": 0.0, "models": rows})
        else:
            rows = [{"model": m["model"], "calls": m["calls"] * scale, "input_tokens": m["input_tokens"] * scale,
                     "output_tokens": m["output_tokens"] * scale, "usd": m["usd"] * scale,
                     "seconds": m["wall_s"] * scale, "rate_measured": True} for m in st["models"]]
            stages_out.append({"stage": st["stage"], "pool": None, "seconds": st["wall_s"] * scale,
                               "partial": bool(st.get("partial")),
                               "calls": st["calls"] * scale, "unserved_calls": 0, "usd": st["usd"] * scale,
                               "models": rows})
    return {
        "from_run": summary["run_id"], "basis": basis, "scale": scale, "chapters": chapters, "chars": chars,
        "thinking_factors": {"tokens": factors[0], "seconds": factors[1]},
        "stages": stages_out,
        "total_seconds": sum(s["seconds"] for s in stages_out),
        "total_usd": sum(s["usd"] for s in stages_out),
        "quota": {name: dict(q, left_after=max(left.get(name, 0), 0)) for name, q in quota.items()},
    }


def _dur(seconds: float) -> str:
    return f"{seconds / 3600:.1f}h" if seconds >= 5400 else f"{seconds / 60:.1f}m"


def _k(n: float) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else f"{n:.0f}"


def format_estimate(fc: dict[str, Any]) -> str:
    r"""A plain-text forecast."""
    size = f"{fc['chapters']} chapters" + (f", {fc['chars'] / 1e6:.2f}M chars" if fc["chars"] else "")
    tf = fc["thinking_factors"]
    lines = [f"forecast for {size}, scaled by {fc['basis']} (x{fc['scale']:.3f}) from run {fc['from_run']}",
             f"thinking models: x{tf['tokens']:.1f} tokens, x{tf['seconds']:.1f} time per call"]
    for st in fc["stages"]:
        tokens = sum(m["input_tokens"] + m["output_tokens"] for m in st["models"])
        head = (f"{st['stage']:<7} {_dur(st['seconds']):>7}  {st['calls']:>8.0f} calls  {_k(tokens):>7} tokens  "
                f"${st['usd']:.4f}" + (f"  pool {st['pool']}" if st["pool"] else ""))
        if st.get("partial"):
            head += "  (from a stage the source run had not finished: too low)"
        if st["unserved_calls"]:
            head += f"  UNSERVED {st['unserved_calls']} calls: pool used up, the run would stop"
        lines.append(head)
        for m in st["models"]:
            lines.append(f"{'':<9}{m['model'][:34]:<34} {m['calls']:>8.0f} calls  {_k(m['input_tokens']):>7} in  "
                         f"{_k(m['output_tokens']):>7} out  {_dur(m['seconds']):>7}"
                         + ("" if m["rate_measured"] else "  (rate not measured on this stage)"))
    lines.append(f"total: {_dur(fc['total_seconds'])}, ${fc['total_usd']:.4f} "
                 "(linear scaling; S2 slows as aliases grow, so long novels run over)")
    if fc["quota"]:
        lines.append("free quota (tokens): model, pool, quota, used, left now, left after this forecast")
        for name, q in fc["quota"].items():
            lines.append(f"  {name:<28} {q['pool']:<14} {_k(q['quota']):>7} {_k(q['used']):>7} "
                         f"{_k(q['left']):>7} {_k(q['left_after']):>7}" + ("  used up" if q["exhausted"] else ""))
    return "\n".join(lines)
