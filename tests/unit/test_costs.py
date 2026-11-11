"""G-33: run cost summary, forecast and the CLI commands on a small fake database."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from xn import costs as costs_mod
from xn import estimate as est
from xn.cli.main import app
from xn.storage.database import connect, init_schema

RUN = "run1"
T0 = "2026-11-01T00:00:00+00:00"


def ts(seconds: float) -> str:
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(T0) + timedelta(seconds=seconds)).isoformat()


def call(conn: sqlite3.Connection, i: int, stage: str, model: str, at: float, *, inp: int | None = 100,
         out: int | None = 20, ok: int = 1, retries: int = 0, cost: float | None = 0.0, ms: int = 1000,
         novel: str = "n1") -> None:
    conn.execute(
        "INSERT INTO llm_runs (run_id, novel_id, stage, chapter_id, model, input_tokens, output_tokens, latency_ms, "
        "retries, ok, cost_usd, egress_class, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"c{i}-{stage}-{novel}", novel, stage, i, model, inp, out, ms, retries, ok, cost, "local", ts(at)),
    )


def job(conn: sqlite3.Connection, ch: int, stage: str, at: float, status: str = "done") -> None:
    conn.execute("INSERT INTO jobs (novel_id, chapter_id, stage, status, updated_at) VALUES ('n1',?,?,?,?)",
                 (ch, stage, status, ts(at)))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "xn.db"
    conn = connect(path)
    init_schema(conn)
    conn.execute("INSERT INTO novels (novel_id, title, genre_profile, total_chapters) VALUES ('n1','N','xianxia',4)")
    for ch in range(1, 5):
        conn.execute("INSERT INTO chapters (novel_id, chapter_id, clean_text, sha256, token_count) "
                     "VALUES ('n1',?,?,?,?)", (ch, "x" * 500, f"h{ch}", 125))
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, novel_id, status, stages, chapter_to, current_stage, stage_times, "
        "started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (RUN, "n1", "done", json.dumps(["s0", "s1", "s2", "s7"]), 4, "s7", json.dumps({"s0": 30.0, "s1": 5.0}),
         T0, ts(100_000)),
    )
    job(conn, 1, "s0", 1)
    # s1: four calls, a pause of one hour between the 2nd and 3rd
    for i, at in enumerate([10, 20, 3620, 3630], start=1):
        call(conn, i, "s1", "local-a", at, ms=10_000)
        job(conn, i, "s1", at)
    # s2: an early pass that was redone later, then the real pass with one failed retried call
    call(conn, 9, "s2", "old-model", 4000)
    for i, at in enumerate([20_000, 20_010], start=1):
        call(conn, i, "s2", "local-a", at)
        job(conn, i, "s2", at)
    call(conn, 3, "s2", "cloud-b", 20_020, inp=None, out=None, ok=0, retries=2, cost=None)
    # s7 on the pool: plain model then a thinking model after failover
    call(conn, 1, "s7", "pool-plain", 30_000, inp=800, out=200, ms=4_000)
    call(conn, 2, "s7", "pool-plain", 30_004, inp=800, out=200, ms=4_000)
    call(conn, 3, "s7", "pool-think", 30_084, inp=4_000, out=3_000, ms=80_000)
    job(conn, 1, "s7", 30_084)
    # another novel, and a call after the run ended: neither counts
    call(conn, 1, "s1", "local-a", 50, novel="other")
    call(conn, 99, "s1", "local-a", 200_000)
    conn.commit()
    conn.close()
    return path


def summary_of(db: Path) -> dict:
    conn = costs_mod.open_readonly(db)
    try:
        return costs_mod.run_costs(conn, RUN)
    finally:
        conn.close()


def stage(summary: dict, name: str) -> dict:
    return next(s for s in summary["stages"] if s["stage"] == name)


def test_active_seconds_leaves_out_pauses() -> None:
    assert costs_mod.active_seconds([0, 10, 20, 2000, 2010], gap_s=600) == 30
    assert costs_mod.active_seconds([5]) == 0


def test_run_costs_per_stage_and_model(db: Path) -> None:
    s = summary_of(db)
    assert (s["chapters"], s["chars"], s["text_tokens"]) == (4, 2000, 500)
    assert [st["stage"] for st in s["stages"]] == ["s0", "s1", "s2", "s7"]
    s1 = stage(s, "s1")
    assert s1["calls"] == 4 and s1["input_tokens"] == 400 and s1["output_tokens"] == 80
    assert s1["wall_s"] == pytest.approx(20)  # 10 + 10, the hour-long pause left out
    assert s1["call_s"] == pytest.approx(40)
    assert s1["per_chapter"]["input_tokens"] == pytest.approx(100)
    assert s1["per_1k_chars"]["calls"] == pytest.approx(2)  # 4 calls over 2k chars
    assert stage(s, "s0")["wall_s"] == pytest.approx(30)  # recorded time wins when longer


def test_failures_retries_and_unknown_cost_are_counted(db: Path) -> None:
    s2 = stage(summary_of(db), "s2")
    assert (s2["calls"], s2["failed"], s2["retries"], s2["unknown_cost"]) == (3, 1, 2, 1)
    cloud = next(m for m in s2["models"] if m["model"] == "cloud-b")
    assert cloud["per_call"] == {}  # no successful call to measure


def test_calls_before_the_last_pass_count_as_redone(db: Path) -> None:
    s = summary_of(db)
    s2 = stage(s, "s2")
    assert [m["model"] for m in s2["models"]] == ["local-a", "cloud-b"]
    assert [m["model"] for m in s2["redone"]] == ["old-model"]
    assert s["redone"]["calls"] == 1
    assert s["totals"]["calls"] == 4 + 3 + 3  # other novel and late calls left out


def test_partial_flag_on_an_unfinished_stage(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("UPDATE pipeline_runs SET finished_at = NULL, status = 'running'")
    conn.commit()
    conn.close()
    conn = costs_mod.open_readonly(db)
    s = costs_mod.run_costs(conn, RUN, now=ts(100_000))
    conn.close()
    assert stage(s, "s7")["partial"] and not stage(s, "s1")["partial"]


def test_open_readonly_refuses_writes(db: Path) -> None:
    conn = costs_mod.open_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM llm_runs")
    conn.close()


def test_write_costs_round_trips(db: Path, tmp_path: Path) -> None:
    s = summary_of(db)
    path = costs_mod.write_costs(s, tmp_path / "runs")
    assert path.name == "run1-costs.json"
    assert json.loads(path.read_text(encoding="utf-8"))["totals"]["calls"] == s["totals"]["calls"]
    assert "redone earlier: old-model" in costs_mod.format_costs(s)


POOL_CONFIG = {
    "model_pools": {"free": {"provider": "p", "extra_body": {"enable_thinking": False}, "models": [
        "pool-plain", {"name": "pool-think", "extra_body": {"enable_thinking": True}, "quota_tokens": 20_000},
        "pool-spare"]}},
    "stage_pools": {"s7": "free"},
}


def test_quota_left_counts_tokens_and_used_up_models(db: Path, tmp_path: Path) -> None:
    other = tmp_path / "copy.db"
    conn = connect(other)
    init_schema(conn)
    call(conn, 1, "s7", "pool-plain", 0, inp=500, out=0)
    conn.execute("INSERT INTO model_quota (novel_id, provider, model, exhausted_at) VALUES ('n1','p','pool-spare',?)",
                 (T0,))
    conn.commit()
    conn.close()
    conns = [costs_mod.open_readonly(db), costs_mod.open_readonly(other)]
    q = est.quota_left(POOL_CONFIG, conns)
    for c in conns:
        c.close()
    assert q["pool-plain"]["used"] == 2_000 + 500 and q["pool-plain"]["left"] == 1_000_000 - 2_500
    assert q["pool-think"]["quota"] == 20_000 and q["pool-think"]["left"] == 13_000 and q["pool-think"]["thinking"]
    assert q["pool-spare"]["exhausted"] and q["pool-spare"]["left"] == 0


def test_estimate_scales_linear_stages_by_chapters_or_chars(db: Path) -> None:
    s = summary_of(db)
    fc = est.estimate(s, {}, {}, chapters=40)
    s1 = next(x for x in fc["stages"] if x["stage"] == "s1")
    assert fc["scale"] == pytest.approx(10) and s1["calls"] == pytest.approx(40)
    assert s1["seconds"] == pytest.approx(200)
    fc2 = est.estimate(s, {}, {}, chapters=4, chars=4_000)
    assert fc2["basis"] == "chars" and fc2["scale"] == pytest.approx(2)


def test_pool_stage_fills_models_in_order_up_to_their_quota(db: Path) -> None:
    s = summary_of(db)
    quota = {
        "pool-plain": {"pool": "free", "quota": 1, "used": 0, "exhausted": False, "left": 5_000},
        "pool-think": {"pool": "free", "quota": 1, "used": 0, "exhausted": False, "left": 14_000},
        "pool-spare": {"pool": "free", "quota": 1, "used": 0, "exhausted": False, "left": 0},
    }
    fc = est.estimate(s, POOL_CONFIG, quota, chapters=40)  # 3 calls x 10 = 30 calls on s7
    s7 = next(x for x in fc["stages"] if x["stage"] == "s7")
    assert s7["pool"] == "free" and s7["calls"] == 30
    rows = {m["model"]: m for m in s7["models"]}
    assert rows["pool-plain"]["calls"] == 5  # 5k tokens left / 1k per call
    assert rows["pool-think"]["calls"] == 2  # 14k left / 7k per call
    assert s7["unserved_calls"] == 23
    assert fc["quota"]["pool-plain"]["left_after"] == 0
    assert "UNSERVED 23 calls" in est.format_estimate(fc)


def test_unmeasured_thinking_member_uses_the_thinking_factor(db: Path) -> None:
    s = summary_of(db)
    s7 = stage(s, "s7")
    s7["models"] = [m for m in s7["models"] if m["model"] == "pool-plain"]
    factors = est.thinking_factors(s, {"pool-think"})
    assert factors == (est.DEFAULT_THINKING_TOKENS, est.DEFAULT_THINKING_SECONDS)
    rate = est._member_rate(s7, {"model": "pool-think", "thinking": True}, factors, {"pool-think"})
    assert rate["input_tokens"] == pytest.approx(800 * 7) and rate["seconds"] == pytest.approx(4 * 20)
    assert not rate["measured"]


def test_thinking_factors_are_measured_where_a_stage_ran_both(db: Path) -> None:
    tok, sec = est.thinking_factors(summary_of(db), {"pool-think"})
    assert tok == pytest.approx(7.0) and sec == pytest.approx(20.0)


def test_raw_dir_size_counts_chapter_files(tmp_path: Path) -> None:
    (tmp_path / "chapter_1.md").write_text("# Chương 1\n\nabc", encoding="utf-8")
    (tmp_path / "chapter_2.md").write_text("de", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    chapters, chars = est.raw_dir_size(tmp_path)
    assert chapters == 2 and chars > 0
    with pytest.raises(ValueError):
        est.raw_dir_size(tmp_path / "chapter_1.md".replace("chapter_1.md", "") / "missing") if False else \
            est.raw_dir_size(_empty(tmp_path))


def _empty(tmp_path: Path) -> Path:
    d = tmp_path / "empty"
    d.mkdir()
    return d


def test_cli_costs_and_estimate(db: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    before = db.read_bytes()
    res = runner.invoke(app, ["costs", "--run", RUN, "--db", str(db), "--out-dir", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert "s1" in res.output and (tmp_path / "out" / "run1-costs.json").exists()
    configs = tmp_path / "configs"
    configs.mkdir()
    import yaml

    (configs / "config.yaml").write_text(yaml.safe_dump(POOL_CONFIG), encoding="utf-8")
    res = runner.invoke(app, ["estimate", "--chapters", "40", "--from", str(tmp_path / "out" / "run1-costs.json"),
                              "--db", str(db), "--configs-dir", str(configs)])
    assert res.exit_code == 0, res.output
    assert "forecast for 40 chapters" in res.output and "pool-think" in res.output
    res = runner.invoke(app, ["estimate", "--db", str(db)])
    assert res.exit_code == 1 and "exactly one of" in res.output
    assert db.read_bytes() == before  # the report never wrote to the database


def test_ingest_export_writes_next_to_the_database_and_never_raises(db: Path, capsys: pytest.CaptureFixture) -> None:
    from xn.cli.main import _export_costs

    _export_costs(db, RUN)
    assert (db.parent / "runs" / "run1-costs.json").exists()
    _export_costs(db, "no-such-run")  # reported, not raised
    assert "costs not written" in capsys.readouterr().out
