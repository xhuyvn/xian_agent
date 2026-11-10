"""Unit tests for arc prefix summaries with a fake summarizer and a fake local model."""

import json
import re
import warnings
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.usage import RequestUsage

from xn.pipeline.prefix_summaries import (
    STAGE,
    PrefixRequestError,
    arc_prefix_view,
    checkpoints,
    run_prefix,
)
from xn.pipeline.runner import RunRequestError, run_stage
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

NOVEL = "pntt1"
N = 75
SPANS = [(1, 32), (33, 64), (65, 75)]
CONFIG = {
    "total_chapters": N,
    "models": {"local_extractor": {"serving_name": "fake", "output_mode": "native"}},
}
TAG = re.compile(r"C(\d+)\b")


@pytest.fixture(autouse=True)
def quiet_local_mode():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        yield


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "prefix.db")
    init_schema(connection)
    yield connection
    connection.close()


def micro(cid: int) -> str:
    return f"C{cid} Hàn Lập tu luyện ở chương này."


@pytest.fixture
def repo(conn):
    r = Repository(conn)
    r.add_novel(novel_id=NOVEL, title="t", genre_profile="xianxia", total_chapters=N)
    for cid in range(1, N + 1):
        r.save_chapter(
            novel_id=NOVEL, chapter_id=cid, title=None, clean_text="x", sha256=str(cid),
            token_count=1, stage="s0", input_hash=str(cid),
        )
        r.save_extraction(
            novel_id=NOVEL, chapter_id=cid, payload=json.dumps({"micro_summary": micro(cid)}, ensure_ascii=False),
            prompt_version="v", model="m", stage="s1", input_hash="h",
        )
    r.save_arc_boundaries(novel_id=NOVEL, spans=SPANS, stage="s5", input_hash="h", prompt_version="v", model="m")
    return r


class FakeSummarizer:
    """Echoes the chapter tags it was given, so a summary shows exactly what it read."""

    def __init__(self, fail_at: set[int] | None = None) -> None:
        self.calls: list[tuple[int, int, list[tuple[int, str]]]] = []
        self.fail_at = fail_at or set()

    def __call__(self, arc_id: int, upto: int, window: list[tuple[int, str]]) -> str:
        self.calls.append((arc_id, upto, window))
        if upto in self.fail_at:
            raise RuntimeError("model down")
        return "Tóm tắt " + " ".join(f"C{t}" for t in TAG.findall(" ".join(text for _, text in window)))


def rows(conn):
    return [
        (r["arc_id"], r["upto_chapter"], r["summary"])
        for r in conn.execute(
            "SELECT * FROM arc_prefix_summaries WHERE novel_id = ? ORDER BY upto_chapter", (NOVEL,)
        )
    ]


def tags(text: str | None) -> list[int]:
    return [int(t) for t in TAG.findall(text or "")]


def test_checkpoints_are_multiples_of_ten_after_the_start():
    assert checkpoints(1, 32) == [10, 20, 30]
    assert checkpoints(33, 64) == [40, 50, 60]
    assert checkpoints(40, 70) == [50, 60, 70]  # a start on a multiple is not its own checkpoint
    assert checkpoints(65, 69) == []


def test_run_writes_one_row_per_checkpoint_with_its_job(repo, conn):
    fake = FakeSummarizer()
    result = run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=fake)
    assert (result.arcs, result.processed, result.written, result.skipped, result.failed) == (3, 7, 7, 0, 0)
    assert [(a, u) for a, u, _ in rows(conn)] == [(1, 10), (1, 20), (1, 30), (2, 40), (2, 50), (2, 60), (3, 70)]
    for _, upto, _ in rows(conn):
        job = repo.get_job(novel_id=NOVEL, chapter_id=upto, stage=STAGE)
        assert job["status"] == "done" and job["input_hash"]
    # the window of each call is exactly start..upto of its arc
    starts = {i: s for i, (s, _) in enumerate(SPANS, start=1)}
    for arc_id, upto, window in fake.calls:
        assert [cid for cid, _ in window] == list(range(starts[arc_id], upto + 1))


def test_no_stored_summary_reads_a_chapter_after_its_checkpoint(repo, conn):
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())
    for arc_id, upto, summary in rows(conn):
        seen = tags(summary)
        assert seen and max(seen) == upto
        assert min(seen) == SPANS[arc_id - 1][0]


def test_view_never_carries_a_chapter_after_n(repo):
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())
    for n in range(1, N + 1):
        view = arc_prefix_view(repo, novel_id=NOVEL, chapter_max=n)
        start = next(s for s, e in SPANS if s <= n <= e)
        assert view.start_chapter == start and view.chapter_max == n
        seen = tags(view.summary) + [cid for cid, _ in view.tail] + [t for _, text in view.tail for t in tags(text)]
        assert seen and max(seen) <= n
        assert min(seen) >= start  # nothing from an earlier arc either
        covered = (list(range(start, view.upto_chapter + 1)) if view.upto_chapter else []) + [c for c, _ in view.tail]
        assert covered == list(range(start, n + 1))


@pytest.mark.parametrize(
    "n, upto, tail",
    [
        (9, None, list(range(1, 10))),  # before the first checkpoint
        (10, 10, []),  # at a checkpoint
        (17, 10, list(range(11, 18))),  # between checkpoints
        (32, 30, [31, 32]),  # after the last checkpoint, at the arc end
        (33, None, [33]),  # first chapter of the next arc
        (69, None, list(range(65, 70))),  # below the only checkpoint of arc 3
    ],
)
def test_view_snaps_down_to_the_nearest_checkpoint(repo, n, upto, tail):
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())
    view = arc_prefix_view(repo, novel_id=NOVEL, chapter_max=n)
    assert view.upto_chapter == upto
    assert [c for c, _ in view.tail] == tail
    assert view.tail == [(c, micro(c)) for c in tail]


def test_view_falls_back_to_raw_tail_when_a_checkpoint_is_missing(repo):
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer(fail_at={20}))
    view = arc_prefix_view(repo, novel_id=NOVEL, chapter_max=25)
    assert view.upto_chapter == 10
    assert [c for c, _ in view.tail] == list(range(11, 26))


def test_view_outside_any_arc_is_none(repo):
    assert arc_prefix_view(repo, novel_id=NOVEL, chapter_max=N + 5) is None
    assert arc_prefix_view(repo, novel_id="other", chapter_max=10) is None


def test_rerun_on_unchanged_input_skips_every_checkpoint(repo, conn):
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())
    before = rows(conn)
    fake = FakeSummarizer()
    result = run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=fake)
    assert (result.written, result.skipped, result.failed) == (0, 7, 0)
    assert fake.calls == [] and rows(conn) == before


def test_changed_micro_summary_reruns_only_checkpoints_that_read_it(repo, conn):
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())
    conn.execute(
        "UPDATE extractions SET payload = ? WHERE novel_id = ? AND chapter_id = 45",
        (json.dumps({"micro_summary": "C45 đổi nội dung."}), NOVEL),
    )
    conn.commit()
    fake = FakeSummarizer()
    result = run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=fake)
    assert sorted(u for _, u, _ in fake.calls) == [50, 60]
    assert (result.written, result.skipped) == (2, 5)


def test_failed_checkpoint_is_isolated_and_retried(repo, conn):
    result = run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer(fail_at={40}))
    assert (result.written, result.failed) == (6, 1)
    assert 40 not in [u for _, u, _ in rows(conn)]
    job = repo.get_job(novel_id=NOVEL, chapter_id=40, stage=STAGE)
    assert job["status"] == "failed" and "model down" in job["error"]
    fake = FakeSummarizer()
    result = run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=fake)
    assert [u for _, u, _ in fake.calls] == [40]
    assert (result.written, result.skipped, result.failed) == (1, 6, 0)


def test_resegmentation_prunes_stale_checkpoints(repo, conn):
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())
    repo.save_arc_boundaries(
        novel_id=NOVEL, spans=[(1, 32), (33, 75)], stage="s5", input_hash="h2", prompt_version="v", model="m"
    )
    fake = FakeSummarizer()
    result = run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=fake)
    assert result.pruned == 1  # (3, 70) is gone; (2, 70) replaces it
    assert [(a, u) for a, u, _ in rows(conn)] == [(1, 10), (1, 20), (1, 30), (2, 40), (2, 50), (2, 60), (2, 70)]
    assert [u for _, u, _ in fake.calls] == [70]
    assert repo.get_job(novel_id=NOVEL, chapter_id=70, stage=STAGE)["status"] == "done"
    view = arc_prefix_view(repo, novel_id=NOVEL, chapter_max=72)
    assert (view.arc_id, view.upto_chapter, [c for c, _ in view.tail]) == (2, 70, [71, 72])


def test_prune_drops_jobs_unless_a_kept_checkpoint_shares_the_chapter(repo, conn):
    for arc_id, upto in [(1, 10), (2, 20), (3, 20)]:
        repo.save_arc_prefix_summary(
            novel_id=NOVEL, arc_id=arc_id, upto_chapter=upto, summary="s", stage=STAGE,
            input_hash="h", prompt_version="v", model="m",
        )
    pruned = repo.prune_arc_prefix_summaries(novel_id=NOVEL, keep=[(3, 20)], upto_max=N, stage=STAGE)
    assert pruned == 2
    assert [(a, u) for a, u, _ in rows(conn)] == [(3, 20)]
    assert repo.get_job(novel_id=NOVEL, chapter_id=10, stage=STAGE) is None
    assert repo.get_job(novel_id=NOVEL, chapter_id=20, stage=STAGE) is not None


def test_run_to_a_chapter_inside_an_arc_builds_only_checkpoints_up_to_it(repo, conn):
    fake = FakeSummarizer()
    result = run_prefix(repo, novel_id=NOVEL, config=CONFIG, chapter_to=55, summarizer=fake)
    assert (result.arcs, result.processed) == (2, 5)
    assert max(u for _, u, _ in rows(conn)) == 50
    assert max(cid for _, _, window in fake.calls for cid, _ in window) <= 55


def test_refuses_without_arcs_or_micro_summaries(repo, conn):
    conn.execute("DELETE FROM extractions WHERE novel_id = ? AND chapter_id = 12", (NOVEL,))
    conn.commit()
    with pytest.raises(PrefixRequestError, match="first: 12"):
        run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())
    conn.execute("UPDATE chapters SET arc_id = NULL")
    conn.execute("DELETE FROM arcs")
    conn.commit()
    with pytest.raises(PrefixRequestError, match="run stage s5"):
        run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=FakeSummarizer())


def test_local_model_path_logs_runs_and_sends_only_the_window(repo, conn):
    prompts: list[str] = []

    def respond(messages: list[Any], info: Any) -> ModelResponse:
        user = messages[-1].parts[-1].content
        prompts.append(user)
        text = "Tóm tắt phần đầu mạch truyện: " + " ".join(TAG.findall(user)) + " " + "Hàn Lập tu luyện. " * 12
        return ModelResponse(
            parts=[TextPart(json.dumps({"text": text}, ensure_ascii=False))],
            usage=RequestUsage(input_tokens=10, output_tokens=10),
        )

    model = FunctionModel(respond, profile=ModelProfile(supports_json_schema_output=True))
    result = run_stage(repo, stage="prefix", novel_id=NOVEL, config=CONFIG, chapter_to=32, model=model)
    assert (result.written, result.failed) == (3, 0)
    assert prompts[0].startswith("<upto>10</upto>")
    assert max(tags(prompts[0])) == 10 and max(tags(prompts[-1])) == 30
    runs = conn.execute("SELECT stage, chapter_id, ok FROM llm_runs ORDER BY chapter_id").fetchall()
    assert [tuple(r) for r in runs] == [(STAGE, 10, 1), (STAGE, 20, 1), (STAGE, 30, 1)]


def test_runner_refuses_unsupported_options(repo):
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="prefix", novel_id=NOVEL, config=CONFIG, chapter_from=5)
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="prefix", novel_id=NOVEL, config=CONFIG, stale_only=True)


def test_story_reads_honour_the_bound(repo):
    assert repo.get_arc_at(novel_id=NOVEL, chapter_max=40)["arc_id"] == 2
    assert set(repo.get_arc_at(novel_id=NOVEL, chapter_max=40).keys()) == {"arc_id", "start_chapter"}
    got = repo.list_micro_summaries(novel_id=NOVEL, chapter_from=8, chapter_max=11)
    assert [(r["chapter_id"], r["micro_summary"]) for r in got] == [(c, micro(c)) for c in range(8, 12)]
    assert repo.list_micro_summaries(novel_id="other", chapter_from=1, chapter_max=N) == []


# --- model pools (G-32) ---------------------------------------------------------------------------


def test_a_used_up_pool_stops_the_run_and_resume_continues(repo, conn):
    from xn.model_pool import PoolExhaustedError

    class Exhausting(FakeSummarizer):
        def __call__(self, arc_id, upto, window):
            if upto == 40:
                raise PoolExhaustedError("model pool qwen_free: every model's quota is used up")
            return super().__call__(arc_id, upto, window)

    with pytest.raises(PoolExhaustedError):
        run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=Exhausting())
    assert [u for _, u, _ in rows(conn)] == [10, 20, 30]
    assert repo.get_job(novel_id=NOVEL, chapter_id=40, stage=STAGE)["status"] == "failed"
    fake = FakeSummarizer()
    run_prefix(repo, novel_id=NOVEL, config=CONFIG, summarizer=fake)
    assert [u for _, u, _ in fake.calls] == [40, 50, 60, 70]


def test_prefix_asks_for_its_stage_model(repo, monkeypatch):
    from xn.pipeline import prefix_summaries
    asked = []

    class Stop(Exception):
        pass

    def fake_stage_model(config, stage, *, repo=None, novel_id=None):
        asked.append((stage, novel_id, repo is not None))
        raise Stop

    monkeypatch.setattr(prefix_summaries, "stage_model", fake_stage_model)
    with pytest.raises(Stop):
        run_prefix(repo, novel_id=NOVEL, config=CONFIG)
    assert asked == [("prefix", NOVEL, True)]
