"""Unit tests for stage S6 with a fake OpenRouter model and the in-memory Qdrant client."""

import json
import warnings

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from qdrant_client import QdrantClient

from xn.pipeline.runner import RunRequestError, run_stage
from xn.pipeline.s6_synthesize import STAGE, S6RequestError, run_s6
from xn.schemas.arc import StoryArc
from xn.schemas.extraction import ChapterExtraction
from xn.storage.database import connect, init_schema
from xn.storage.qdrant import QdrantStore
from xn.storage.repository import Repository

NOVEL = "pntt1"
N = 90
SPANS = [(1, 30), (31, 60), (61, 90)]
MODEL = "deepseek/fake-macro"
FILLER = "Hàn Lập tu luyện trong động phủ. " * 7
CONFIG = {
    "total_chapters": N,
    "daily_spend_cap_usd": 1.0,
    "models": {"macro_synthesizer": {"name": MODEL, "temperature": 0.3}},
    "qdrant": {"url": "http://x", "collections": {"chunks": "c", "arcs": "a"}, "dense_size": 4},
}


def synthesis(title="Thất Huyền Môn"):
    return {
        "title": title,
        "primary_locations": ["Thất Huyền Môn"],
        "core_conflict": "Hàn Lập tìm cách tu luyện.",
        "climax_event": "Hàn Lập đột phá.",
        "resolution": "Hàn Lập rời núi.",
        "power_progression": "Luyện Khí tầng 1 lên tầng 3.",
        "key_entities": ["Hàn Lập", "Mặc đại phu"],
        "summary": "Hàn Lập vào Thất Huyền Môn và bắt đầu tu luyện.",
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
    return QdrantStore.from_config(CONFIG, client)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "s6.db")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn):
    r = Repository(conn)
    r.add_novel(novel_id=NOVEL, title="t", genre_profile="xianxia", total_chapters=N)
    for cid in range(1, N + 1):
        add_chapter(r, cid)
    r.save_arc_boundaries(novel_id=NOVEL, spans=SPANS, stage="s5", input_hash="h", prompt_version="v", model="m")
    return r


def add_chapter(repo, chapter_id, summary=None):
    repo.save_chapter(
        novel_id=NOVEL, chapter_id=chapter_id, title=None, clean_text="x", sha256=str(chapter_id),
        token_count=1, stage="s0", input_hash=str(chapter_id),
    )
    repo.save_extraction(
        novel_id=NOVEL, chapter_id=chapter_id,
        payload=ChapterExtraction(
            chapter_id=chapter_id, map_location="x", location_change=False, realm_breakthrough=False,
            major_conflict=False, time_skip_years=0, events=[],
            micro_summary=summary or f"Tóm tắt chương {chapter_id}. " + FILLER,
        ).model_dump_json(),
        prompt_version="v", model="m", stage="s1", input_hash="h",
    )


class FakeCloud:
    """Model factory that answers with a StoryArcSynthesis tool call, or raises when told to."""

    def __init__(self, fail_on=(), cost=0.01):
        self.fail_on = set(fail_on)  # arc start chapters whose call raises
        self.cost = cost
        self.prompts = []
        self.names = []
        self.max_tokens = []

    def __call__(self, name):
        self.names.append(name)

        def respond(messages, info: AgentInfo):
            prompt = str(messages[-1].parts[-1].content)
            self.prompts.append(prompt)
            self.max_tokens.append(info.model_settings["max_tokens"])
            if any(f"từ chương {s} " in prompt for s in self.fail_on):
                raise ValueError("bad output")
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, synthesis())],
                usage=RequestUsage(input_tokens=100, output_tokens=50),
                provider_details={"cost": self.cost},
            )

        return FunctionModel(respond)


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self):
        self.texts = []

    def embed(self, texts):
        self.texts += texts
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def run(repo, store, cloud, embedder=None, config=CONFIG, **kwargs):
    return run_s6(
        repo, novel_id=NOVEL, config=config, store=store, embedder=embedder or FakeEmbedder(),
        cloud_factory=cloud, sleep=lambda s: None, **kwargs,
    )


def arc_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM arcs ORDER BY arc_id")]


def arc_points(client):
    points, _ = client.scroll("a", limit=100, with_payload=True)
    return {p.payload["arc_id"]: p.payload for p in points}


def test_run_s6_fills_every_arc_and_its_point(repo, conn, store, client):
    cloud = FakeCloud()
    embedder = FakeEmbedder()
    result = run(repo, store, cloud, embedder)

    assert (result.arcs, result.written, result.skipped, result.failed) == (3, 3, 0, 0)
    assert result.cost_usd == pytest.approx(0.03)
    for row, (start, end) in zip(arc_rows(conn), SPANS):
        story = StoryArc.model_validate_json(row["payload"])
        assert (story.arc_id, story.start_chapter, story.end_chapter) == (row["arc_id"], start, end)
        assert row["model"] == MODEL and row["prompt_version"]
        job = repo.get_job(novel_id=NOVEL, chapter_id=end, stage=STAGE)
        assert job["status"] == "done"
    points = arc_points(client)
    assert sorted(points) == [1, 2, 3]
    assert points[2]["start_chapter"] == 31 and points[2]["end_chapter"] == 60
    assert embedder.texts[0] == "Thất Huyền Môn\nHàn Lập vào Thất Huyền Môn và bắt đầu tu luyện."


def test_each_arc_sends_only_its_own_micro_summaries(repo, store):
    cloud = FakeCloud()
    run(repo, store, cloud)
    second = cloud.prompts[1]
    assert 'chapter="31" kind="micro_summary"' in second
    assert 'chapter="60" kind="micro_summary"' in second
    assert 'chapter="30"' not in second and 'chapter="61"' not in second
    assert cloud.names == [MODEL] * 3
    assert cloud.max_tokens == [8000] * 3


def test_llm_runs_record_compact_egress_with_the_arc_chapters(repo, conn, store):
    run(repo, store, FakeCloud())
    rows = conn.execute("SELECT * FROM llm_runs WHERE stage = ? ORDER BY created_at", (STAGE,)).fetchall()
    assert len(rows) == 3
    assert {r["egress_class"] for r in rows} == {"compact"}
    assert rows[0]["payload_chapters"] == ",".join(str(c) for c in range(1, 31))


def test_rerun_on_unchanged_input_makes_no_call_and_keeps_rows(repo, conn, store):
    run(repo, store, FakeCloud())
    before = arc_rows(conn)
    cloud = FakeCloud()
    result = run(repo, store, cloud)
    assert (result.written, result.skipped, result.cost_usd) == (0, 3, 0.0)
    assert cloud.prompts == []
    assert arc_rows(conn) == before


def test_changed_summary_resynthesizes_only_that_arc(repo, store):
    run(repo, store, FakeCloud())
    repo.save_extraction(
        novel_id=NOVEL, chapter_id=45,
        payload=ChapterExtraction(
            chapter_id=45, map_location="x", location_change=False, realm_breakthrough=False,
            major_conflict=False, time_skip_years=0, events=[], micro_summary="Đổi. " + FILLER,
        ).model_dump_json(),
        prompt_version="v", model="m", stage="s1", input_hash="h2",
    )
    cloud = FakeCloud()
    result = run(repo, store, cloud)
    assert (result.written, result.skipped) == (1, 2)
    assert "từ chương 31 " in cloud.prompts[0]


def test_s5_resegmentation_resynthesizes_and_drops_stale_points(repo, conn, store, client):
    run(repo, store, FakeCloud())
    repo.save_arc_boundaries(
        novel_id=NOVEL, spans=[(1, 30), (31, 90)], stage="s5", input_hash="h2", prompt_version="v", model="m"
    )
    result = run(repo, store, FakeCloud())
    assert (result.arcs, result.written, result.skipped) == (2, 1, 1)
    assert sorted(arc_points(client)) == [1, 2]
    assert arc_points(client)[2]["end_chapter"] == 90


def test_one_failed_arc_does_not_stop_the_others(repo, conn, store):
    result = run(repo, store, FakeCloud(fail_on={31}))
    assert (result.written, result.failed) == (2, 1)
    rows = arc_rows(conn)
    assert rows[1]["payload"] is None and rows[0]["payload"] and rows[2]["payload"]
    assert repo.get_job(novel_id=NOVEL, chapter_id=60, stage=STAGE)["status"] == "failed"
    retry = run(repo, store, FakeCloud())
    assert (retry.written, retry.skipped) == (1, 2)


def test_spend_cap_stops_the_run_before_any_call(repo, conn, store):
    cloud = FakeCloud()
    result = run(repo, store, cloud, config={**CONFIG, "daily_spend_cap_usd": 0.0})
    assert result.stopped_at == 1 and result.written == 0
    assert cloud.prompts == []
    assert all(r["payload"] is None for r in arc_rows(conn))


def test_spend_cap_reached_mid_run_stops_there(repo, conn, store):
    result = run(repo, store, FakeCloud(cost=0.6), config={**CONFIG, "daily_spend_cap_usd": 1.0})
    assert (result.written, result.stopped_at) == (2, 3)


def test_unset_spend_cap_refuses(repo, store):
    config = {k: v for k, v in CONFIG.items() if k != "daily_spend_cap_usd"}
    cloud = FakeCloud()
    result = run(repo, store, cloud, config=config)
    assert result.stopped_at == 1 and cloud.prompts == []


def test_arc_over_the_evidence_budget_breaks_the_egress_rule_and_fails(repo, conn, store):
    for cid in range(1, N + 1):
        add_chapter(repo, cid, summary="tu luyện " * 133)  # 1197 chars, near the S1 maximum
    repo.save_arc_boundaries(
        novel_id=NOVEL, spans=[(1, N)], stage="s5", input_hash="h2", prompt_version="v", model="m"
    )
    cloud = FakeCloud()
    result = run(repo, store, cloud)
    assert (result.written, result.failed) == (0, 1)
    assert cloud.prompts == []
    assert "budget" in repo.get_job(novel_id=NOVEL, chapter_id=N, stage=STAGE)["error"]


def test_chapter_to_limits_the_arcs(repo, conn, store):
    result = run(repo, store, FakeCloud(), chapter_to=60)
    assert (result.arcs, result.written) == (2, 2)
    assert arc_rows(conn)[2]["payload"] is None


def test_no_arcs_is_refused(repo, store):
    with pytest.raises(S6RequestError, match="run s5 first"):
        run(repo, store, FakeCloud(), chapter_to=20)


def test_missing_extraction_is_refused(repo, conn, store):
    conn.execute("DELETE FROM extractions WHERE chapter_id = 40")
    conn.commit()
    with pytest.raises(S6RequestError, match="arc 2"):
        run(repo, store, FakeCloud())


def test_save_arc_payload_refuses_a_changed_span(repo, conn):
    with pytest.raises(ValueError, match="not in arcs"):
        repo.save_arc_payload(
            novel_id=NOVEL, arc_id=2, start_chapter=31, end_chapter=61, payload=json.dumps({}),
            stage=STAGE, input_hash="h", prompt_version="v", model="m",
        )
    assert arc_rows(conn)[1]["payload"] is None
    assert repo.get_job(novel_id=NOVEL, chapter_id=61, stage=STAGE) is None


def test_runner_runs_s6(repo, store):
    result = run_stage(
        repo, stage="s6", novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder(),
        cloud_factory=FakeCloud(),
    )
    assert result.written == 3


@pytest.mark.parametrize("kwargs", [{"stale_only": True}, {"chapter_from": 31}])
def test_runner_refuses_partial_s6(repo, store, kwargs):
    with pytest.raises(RunRequestError, match="s6"):
        run_stage(repo, stage="s6", novel_id=NOVEL, config=CONFIG, store=store, **kwargs)
