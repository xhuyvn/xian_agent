"""Unit tests for stage S7 with a fake local model."""

import json
import warnings

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.usage import RequestUsage

from xn.pipeline.runner import RunRequestError, run_stage
from xn.pipeline.s7_digest import (
    STAGE,
    S7RequestError,
    S7Settings,
    checkpoints,
    fit_budget,
    run_s7,
)
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

NOVEL = "pntt1"
N = 40
ARCS = [(1, 25), (26, 40)]
CONFIG = {
    "total_chapters": N,
    "models": {"local_extractor": {"serving_name": "fake", "output_mode": "native"}},
}
GOOD = "chữ " * 100
SHORT = "chữ " * 20

# entity_id -> (name, first chapter, chapters of its events)
HAN_LAP = 1  # 3 events by chapter 10, a 4th at 22: prefix at 10, nothing new at 20, full at 25
TRUONG = 2  # only 2 events in arc 1: no digest
MAC = 3  # 3 events in arc 2, all after 30: full digest at 40 only
ENTITIES = {
    HAN_LAP: ("Hàn Lập", 1, [2, 5, 8, 22]),
    TRUONG: ("Trương Thiết", 3, [3, 4]),
    MAC: ("Mặc đại phu", 31, [31, 33, 35]),
}


@pytest.fixture(autouse=True)
def quiet_local_mode():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        yield


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "s7.db")
    init_schema(connection)
    yield connection
    connection.close()


def event(entity_id, chapter_id, ordinal=0, event_type="ACQUISITION", text=None):
    return {
        "id": chapter_id * 100 + entity_id * 10 + ordinal, "entity_id": entity_id, "event_type": event_type,
        "ordinal": ordinal, "description": text or f"Sự kiện của {entity_id} ở chương {chapter_id}",
    }


def save_chapter_events(repo, chapter_id, events):
    repo.save_ledger(novel_id=NOVEL, chapter_id=chapter_id, events=events, stage="s3", input_hash="h")


@pytest.fixture
def repo(conn):
    r = Repository(conn)
    r.add_novel(novel_id=NOVEL, title="t", genre_profile="xianxia", total_chapters=N)
    for cid in range(1, N + 1):
        r.save_chapter(
            novel_id=NOVEL, chapter_id=cid, title=None, clean_text="x", sha256=str(cid),
            token_count=1, stage="s0", input_hash=str(cid),
        )
    for entity_id, (name, first, _) in ENTITIES.items():
        conn.execute(
            "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) "
            "VALUES (?, ?, ?, 'nhân vật', ?)",
            (NOVEL, entity_id, name, first),
        )
    conn.commit()
    by_chapter = {}
    for entity_id, (_, _, chapters) in ENTITIES.items():
        for cid in chapters:
            by_chapter.setdefault(cid, []).append(event(entity_id, cid))
    for cid in range(1, N + 1):
        save_chapter_events(r, cid, by_chapter.get(cid, []))
    r.save_arc_boundaries(
        novel_id=NOVEL, spans=ARCS, stage="s5", input_hash="h", prompt_version="v", model="m"
    )
    return r


def user_prompts(messages):
    return [
        part.content
        for msg in messages
        for part in msg.parts
        if getattr(part, "part_kind", None) == "user-prompt"
    ]


class FakeModel:
    """A native-output fake that records each user prompt and answers from a script."""

    def __init__(self, answers=None):
        self.prompts = []
        self.requests = 0
        self.answers = list(answers or [])

    def respond(self, messages, info):
        self.requests += 1
        self.prompts.extend(p for p in user_prompts(messages) if p not in self.prompts)
        text = self.answers.pop(0) if self.answers else GOOD
        content = json.dumps({"digest": text}, ensure_ascii=False)
        return ModelResponse(parts=[TextPart(content)], usage=RequestUsage(input_tokens=10, output_tokens=10))

    def model(self):
        return FunctionModel(self.respond, profile=ModelProfile(supports_json_schema_output=True))


def digest_rows(conn):
    return {
        (row[0], row[1], row[2]): row[3]
        for row in conn.execute(
            "SELECT entity_id, arc_id, upto_chapter, event_count FROM entity_arc_digests WHERE novel_id = ?",
            (NOVEL,),
        )
    }


def test_checkpoints_are_multiples_of_ten_inside_the_arc_then_its_end():
    assert checkpoints(1, 25, 10) == [10, 20, 25]
    assert checkpoints(26, 40, 10) == [30, 40]
    assert checkpoints(30, 60, 10) == [30, 40, 50, 60]
    assert checkpoints(41, 45, 10) == [45]


def test_threshold_and_checkpoint_rule(repo, conn):
    fake = FakeModel()
    result = run_s7(repo, novel_id=NOVEL, config=CONFIG, model=fake.model())

    assert digest_rows(conn) == {
        (HAN_LAP, 1, 10): 3,  # prefix: 3 events by chapter 10
        (HAN_LAP, 1, 25): 4,  # full; chapter 20 had nothing new, so no prefix there
        (MAC, 2, 40): 3,  # 0 events by chapter 30, so no prefix
    }
    assert (result.processed, result.skipped, result.failed) == (2, 0, 0)
    assert (result.written, result.full, result.prefix) == (3, 2, 1)
    for _, end in ARCS:
        assert repo.get_job(novel_id=NOVEL, chapter_id=end, stage=STAGE)["status"] == "done"
    assert fake.requests == 3


def test_prefix_digest_never_sees_a_later_event(repo, conn):
    save_chapter_events(repo, 22, [event(HAN_LAP, 22, text="BÍ MẬT SAU CHƯƠNG MƯỜI")])
    fake = FakeModel()
    run_s7(repo, novel_id=NOVEL, config=CONFIG, model=fake.model())

    prefix = [p for p in fake.prompts if "<upto>10</upto>" in p]
    full = [p for p in fake.prompts if "<upto>25</upto>" in p]
    assert len(prefix) == 1 and len(full) == 1
    assert "BÍ MẬT" not in prefix[0] and "[Chương 22]" not in prefix[0]
    assert "[Chương 8]" in prefix[0] and "<entity>Hàn Lập</entity>" in prefix[0]
    assert "BÍ MẬT" in full[0]
    # the bounded read L3 relies on: digests up to N=10 hold only the prefix row
    rows = repo.get_entity_arc_digests(novel_id=NOVEL, entity_id=HAN_LAP, chapter_max=24)
    assert [(r["upto_chapter"], r["event_count"]) for r in rows] == [(10, 3)]


def test_digest_outside_the_word_range_goes_back_to_the_model(repo, conn):
    fake = FakeModel(answers=[SHORT, "chữ " * 200, GOOD])
    run_s7(repo, novel_id=NOVEL, config=CONFIG, model=fake.model())
    assert fake.requests == 5  # 3 for the first digest, 1 each for the other two
    stored = conn.execute("SELECT digest FROM entity_arc_digests WHERE upto_chapter = 10").fetchone()[0]
    assert len(stored.split()) == 100


def test_a_failed_arc_keeps_its_old_rows_and_the_next_arc_still_runs(repo, conn):
    run_s7(repo, novel_id=NOVEL, config=CONFIG, model=FakeModel().model())
    before = digest_rows(conn)
    save_chapter_events(repo, 9, [event(HAN_LAP, 9)])  # arc 1 changes
    save_chapter_events(repo, 36, [event(MAC, 36)])  # arc 2 changes

    fake = FakeModel(answers=[SHORT] * 4)  # arc 1's first digest fails after 3 retries
    result = run_s7(repo, novel_id=NOVEL, config=CONFIG, model=fake.model())

    assert (result.processed, result.failed) == (1, 1)
    rows = digest_rows(conn)
    assert {k: v for k, v in rows.items() if k[1] == 1} == {k: v for k, v in before.items() if k[1] == 1}
    assert rows[(MAC, 2, 40)] == 4
    job = repo.get_job(novel_id=NOVEL, chapter_id=25, stage=STAGE)
    assert job["status"] == "failed" and job["error"]


def test_unchanged_input_skips_every_arc_without_a_model_call(repo, conn):
    run_s7(repo, novel_id=NOVEL, config=CONFIG, model=FakeModel().model())
    rows = digest_rows(conn)

    fake = FakeModel()
    result = run_s7(repo, novel_id=NOVEL, config=CONFIG, model=fake.model())
    assert (result.processed, result.skipped, result.written) == (0, 2, 0)
    assert fake.requests == 0
    assert digest_rows(conn) == rows

    save_chapter_events(repo, 38, [event(MAC, 38)])
    fake = FakeModel()
    result = run_s7(repo, novel_id=NOVEL, config=CONFIG, model=fake.model())
    assert (result.processed, result.skipped) == (1, 1)
    assert fake.requests == 1 and digest_rows(conn)[(MAC, 2, 40)] == 4


def test_only_arcs_ending_by_chapter_to_run(repo, conn):
    result = run_s7(repo, novel_id=NOVEL, config=CONFIG, chapter_to=30, model=FakeModel().model())
    assert result.processed == 1
    assert {k[1] for k in digest_rows(conn)} == {1}


def test_refuses_without_arcs_or_without_the_ledger(repo, conn):
    with pytest.raises(S7RequestError, match="No arc"):
        run_s7(repo, novel_id=NOVEL, config=CONFIG, chapter_to=20, model=FakeModel().model())
    conn.execute("DELETE FROM jobs WHERE stage = 's3' AND chapter_id = 30")
    conn.commit()
    with pytest.raises(S7RequestError, match="first: 30"):
        run_s7(repo, novel_id=NOVEL, config=CONFIG, model=FakeModel().model())
    assert digest_rows(conn) == {}


def test_budget_drops_mentioned_events_first():
    rows = [
        {"chapter_id": c, "event_type": t, "tier_or_realm": None, "description": "x" * 290}
        for c, t in [(1, "MENTIONED"), (2, "ACQUISITION"), (3, "MENTIONED"), (4, "UPGRADE")]
    ]
    assert fit_budget(rows, 1000) == rows
    assert [r["chapter_id"] for r in fit_budget(rows, 250)] == [2, 4]
    assert [r["chapter_id"] for r in fit_budget(rows, 120)] == [2]


def test_settings_defaults():
    s = S7Settings.from_config({})
    assert (s.min_events, s.checkpoint_every, s.min_words, s.max_words) == (3, 10, 80, 150)


def test_runner_runs_s7_and_refuses_unsupported_options(repo, conn):
    result = run_stage(repo, stage="s7", novel_id=NOVEL, config=CONFIG, model=FakeModel().model())
    assert result.written == 3
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s7", novel_id=NOVEL, config=CONFIG, stale_only=True)
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s7", novel_id=NOVEL, config=CONFIG, chapter_from=2)


def test_list_events_between_honours_both_bounds_and_the_tenant(repo, conn):
    conn.execute(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, description) "
        "VALUES ('other', 1, 5, 1, 'MENTIONED', 0, 'x')"
    )
    rows = repo.list_events_between(novel_id=NOVEL, chapter_from=3, chapter_max=8)
    assert sorted({r["chapter_id"] for r in rows}) == [3, 4, 5, 8]
    assert {r["novel_id"] for r in rows} == {NOVEL}
