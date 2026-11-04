"""Unit tests for stage S5 with a fake reviewer and the in-memory Qdrant client."""

import warnings

import pytest
from qdrant_client import QdrantClient, models

from xn.pipeline.runner import RunRequestError, run_stage
from xn.pipeline.s5_segment import (
    JOB_CHAPTER,
    STAGE,
    S5RequestError,
    S5Settings,
    boundary_f1,
    change_scores,
    chapter_score,
    review_boundaries,
    run_s5,
    segment,
    smooth,
    spans_from_starts,
)
from xn.schemas.extraction import ChapterExtraction
from xn.storage.database import connect, init_schema
from xn.storage.qdrant import ChunkPoint, QdrantStore
from xn.storage.repository import Repository

NOVEL = "pntt1"
SUMMARY = "Hàn Lập tu luyện trong động phủ. " * 10
N = 100
PEAKS = (31, 66)  # chapters whose flags are all on
CONFIG = {
    "total_chapters": N,
    "arc_detection": {"min_arc_length": 30, "max_arc_length": 90, "boundary_penalty": 0.3, "smoothing_window": 1},
    "models": {"local_extractor": {"serving_name": "fake"}},
    "qdrant": {"url": "http://x", "collections": {"chunks": "c", "arcs": "a"}, "dense_size": 4},
}


@pytest.fixture(autouse=True)
def quiet_local_mode():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        yield


@pytest.fixture
def client():
    return QdrantClient(":memory:")


@pytest.fixture
def store(client):
    s = QdrantStore.from_config(CONFIG, client)
    s.ensure_collections()
    return s


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "s5.db")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn):
    r = Repository(conn)
    r.add_novel(novel_id=NOVEL, title="t", genre_profile="xianxia", total_chapters=N)
    for cid in range(1, N + 1):
        add_chapter(r, cid, peak=cid in PEAKS)
    return r


def extraction(chapter_id, *, peak=False, summary=SUMMARY, time_skip=0.0):
    return ChapterExtraction(
        chapter_id=chapter_id,
        map_location="x",
        location_change=peak,
        realm_breakthrough=peak,
        major_conflict=peak,
        time_skip_years=time_skip,
        micro_summary=summary,
        events=[],
    )


def add_chapter(repo, chapter_id, *, peak=False, summary=SUMMARY):
    repo.save_chapter(
        novel_id=NOVEL, chapter_id=chapter_id, title=None, clean_text="x", sha256=str(chapter_id),
        token_count=1, stage="s0", input_hash=str(chapter_id),
    )
    repo.save_extraction(
        novel_id=NOVEL, chapter_id=chapter_id,
        payload=extraction(chapter_id, peak=peak, summary=summary).model_dump_json(),
        prompt_version="v", model="m", stage="s1", input_hash="h",
    )


def add_point(store, chapter_id, arc_id=None):
    store.upsert_chunks(
        novel_id=NOVEL,
        points=[
            ChunkPoint(
                chapter_id=chapter_id, chunk_no=0, kind="paragraph", text="t", entity_ids=[],
                arc_id=arc_id, dense=[1.0, 0.0, 0.0, 0.0], sparse_indices=[1], sparse_values=[1.0],
            )
        ],
    )


def point_arcs(client):
    points, _ = client.scroll("c", limit=1000, with_payload=True)
    return {p.payload["chapter_id"]: p.payload.get("arc_id") for p in points}


class FakeEmbedder:
    """One direction per gold-like arc, so the change score peaks where the arc changes."""

    model = "fake-embed"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for cid in range(1, len(texts) + 1):
            arc = sum(cid >= p for p in PEAKS)
            out.append([1.0 if d == arc else 0.0 for d in range(4)])
        return out


class FakeReviewer:
    def __init__(self, answer=None, fail=False):
        self.calls = []
        self.answer = answer
        self.fail = fail

    def __call__(self, chapter_id, window):
        self.calls.append((chapter_id, [c for c, _ in window]))
        if self.fail:
            raise RuntimeError("model gone")
        return self.answer(chapter_id) if self.answer else None


def settings(**overrides):
    return S5Settings.from_config({"arc_detection": {"min_arc_length": 30, "max_arc_length": 90, **overrides}})


# --- scoring and DP ---


def test_chapter_score_weights_every_flag_and_any_time_skip():
    weights = settings().weights
    assert chapter_score(extraction(1), weights) == 0.0
    assert chapter_score(extraction(1, peak=True), weights) == pytest.approx(0.9)
    assert chapter_score(extraction(1, time_skip=0.5), weights) == pytest.approx(0.1)


def test_smooth_is_centered_and_truncated_at_the_ends():
    assert smooth([0, 0, 5, 0, 0], 5) == [pytest.approx(5 / 3), 1.25, 1.0, 1.25, pytest.approx(5 / 3)]
    assert smooth([1.0], 5) == [1.0]


def test_segment_cuts_at_peaks_and_keeps_lengths_in_bounds():
    scores = [0.0] * N
    for p in PEAKS:
        scores[p - 1] = 1.0
    starts = segment(scores, min_len=30, max_len=90, penalty=0.3)
    assert starts == [1, *PEAKS]
    lengths = [e - s + 1 for s, e in spans_from_starts(starts, N)]
    assert all(30 <= n <= 90 for n in lengths)


def test_segment_penalty_stops_cuts_on_a_flat_score():
    starts = segment([0.1] * N, min_len=30, max_len=90, penalty=0.3)
    assert len(starts) == 2  # 100 chapters cannot be one arc of at most 90, and no extra cut pays
    assert all(30 <= e - s + 1 <= 90 for s, e in spans_from_starts(starts, N))


def test_segment_forces_cuts_when_the_novel_exceeds_max_len():
    starts = segment([0.0] * 400, min_len=30, max_len=90, penalty=0.3)
    lengths = [e - s + 1 for s, e in spans_from_starts(starts, 400)]
    assert all(30 <= n <= 90 for n in lengths) and sum(lengths) == 400


def test_segment_short_novel_is_one_arc():
    assert segment([1.0] * 10, min_len=30, max_len=90, penalty=0.3) == [1]
    assert segment([], min_len=30, max_len=90, penalty=0.3) == []


def test_segment_is_deterministic():
    scores = [((i * 37) % 11) / 10 for i in range(600)]
    assert segment(scores, min_len=30, max_len=90, penalty=0.3) == segment(
        scores, min_len=30, max_len=90, penalty=0.3
    )


def test_change_scores_peak_where_the_summaries_change():
    vectors = FakeEmbedder().embed([SUMMARY] * N)
    scores = change_scores(vectors, (5, 10))
    assert scores[0] == 0.0 and max(scores) == 1.0
    assert {i + 1 for i, s in enumerate(scores) if s == 1.0} == set(PEAKS)
    assert change_scores([[1.0, 0.0]] * 5, (5,)) == [0.0] * 5


# --- boundary review ---


def test_review_sees_ten_chapters_each_side_and_shifts_at_most_ten():
    summaries = {c: SUMMARY for c in range(1, N + 1)}
    reviewer = FakeReviewer(answer=lambda b: b + 40)
    starts, shifted, failed = review_boundaries(
        [1, 31, 91], n=180, summaries={c: SUMMARY for c in range(1, 181)}, reviewer=reviewer,
        settings=settings(),
    )
    assert reviewer.calls[0] == (31, list(range(21, 42)))
    assert starts == [1, 41, 101] and shifted == 2 and failed == 0
    assert all(30 <= e - s + 1 <= 90 for s, e in spans_from_starts(starts, 180))


def test_review_clamps_to_keep_arc_lengths():
    summaries = {c: SUMMARY for c in range(1, N + 1)}
    # moving 31 back to 21 would leave a first arc of 20 chapters
    starts, _, _ = review_boundaries(
        [1, 31, 66], n=N, summaries=summaries, reviewer=FakeReviewer(answer=lambda b: b - 10),
        settings=settings(),
    )
    assert starts[1] == 31
    assert all(30 <= e - s + 1 <= 90 for s, e in spans_from_starts(starts, N))


def test_review_failure_keeps_the_boundary():
    summaries = {c: SUMMARY for c in range(1, N + 1)}
    starts, shifted, failed = review_boundaries(
        [1, 31, 66], n=N, summaries=summaries, reviewer=FakeReviewer(fail=True), settings=settings()
    )
    assert starts == [1, 31, 66] and shifted == 0 and failed == 2


# --- boundary F1 ---


def test_boundary_f1_matches_one_to_one_within_tolerance():
    score = boundary_f1([1, 33, 70, 72], [1, 31, 71], tolerance=3)
    assert score["hits"] == 2 and score["predicted"] == 3 and score["gold"] == 2
    assert score["precision"] == pytest.approx(2 / 3) and score["recall"] == 1.0
    assert boundary_f1([1, 40], [1, 31])["f1"] == 0.0


# --- the stage ---


def test_run_s5_writes_arc_ids_placeholders_job_and_points(repo, conn, store, client):
    for cid in (1, 31, 65, 66, 100):
        add_point(store, cid)
    result = run_s5(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(), reviewer=FakeReviewer())
    assert result.arcs == 3 and not result.skipped

    arcs = conn.execute("SELECT arc_id, start_chapter, end_chapter, payload FROM arcs ORDER BY arc_id").fetchall()
    assert [tuple(r) for r in arcs] == [(1, 1, 30, None), (2, 31, 65, None), (3, 66, 100, None)]
    chapter_arcs = dict(conn.execute("SELECT chapter_id, arc_id FROM chapters").fetchall())
    assert chapter_arcs[30] == 1 and chapter_arcs[31] == 2 and chapter_arcs[100] == 3
    job = repo.get_job(novel_id=NOVEL, chapter_id=JOB_CHAPTER, stage=STAGE)
    assert job["status"] == "done"
    assert point_arcs(client) == {1: 1, 31: 2, 65: 2, 66: 3, 100: 3}


def test_rerun_on_unchanged_input_skips_and_keeps_rows(repo, conn, store):
    run_s5(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(), reviewer=FakeReviewer())
    before = conn.execute("SELECT * FROM arcs ORDER BY arc_id").fetchall()
    reviewer = FakeReviewer(answer=lambda b: b + 3)
    result = run_s5(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(), reviewer=reviewer)
    assert result.skipped and reviewer.calls == []
    assert conn.execute("SELECT * FROM arcs ORDER BY arc_id").fetchall() == before


def test_changed_input_keeps_payload_of_unchanged_spans(repo, conn, store):
    run_s5(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(), reviewer=FakeReviewer())
    conn.execute("UPDATE arcs SET payload = '{}' WHERE novel_id = ?", (NOVEL,))
    conn.commit()
    add_chapter(repo, 10, summary="Hàn Lập rời động phủ đi tìm linh dược. " * 8)
    reviewer = FakeReviewer(answer=lambda b: b + 2 if b == 66 else None)
    run_s5(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(), reviewer=reviewer)
    rows = conn.execute("SELECT arc_id, start_chapter, end_chapter, payload FROM arcs ORDER BY arc_id").fetchall()
    assert [tuple(r) for r in rows] == [(1, 1, 30, "{}"), (2, 31, 67, None), (3, 68, 100, None)]


def test_run_s5_refuses_missing_extractions(repo, conn, store):
    conn.execute("DELETE FROM extractions WHERE chapter_id = 50")
    conn.commit()
    with pytest.raises(S5RequestError, match="first: 50"):
        run_s5(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(), reviewer=FakeReviewer())
    assert conn.execute("SELECT COUNT(*) FROM arcs").fetchone()[0] == 0


def test_shorter_run_clears_arc_id_past_the_range(repo, conn, store, client):
    add_point(store, 100, arc_id=7)
    run_s5(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(), chapter_to=60, reviewer=FakeReviewer())
    assert point_arcs(client)[100] is None
    assert conn.execute("SELECT arc_id FROM chapters WHERE chapter_id = 100").fetchone()[0] is None


def test_qdrant_failure_marks_job_failed_and_leaves_sqlite(repo, conn, client):
    class BrokenStore(QdrantStore):
        def set_arc_id(self, **kwargs):
            raise RuntimeError("qdrant gone")

    broken = BrokenStore.from_config(CONFIG, client)
    with pytest.raises(RuntimeError):
        run_s5(repo, novel_id=NOVEL, config=CONFIG, store=broken, embedder=FakeEmbedder(), reviewer=FakeReviewer())
    assert repo.get_job(novel_id=NOVEL, chapter_id=JOB_CHAPTER, stage=STAGE)["status"] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM arcs").fetchone()[0] == 0


@pytest.mark.parametrize("kwargs", [{"chapter_from": 2}, {"stale_only": True}])
def test_runner_refuses_partial_s5(repo, kwargs):
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s5", novel_id=NOVEL, config=CONFIG, **kwargs)
