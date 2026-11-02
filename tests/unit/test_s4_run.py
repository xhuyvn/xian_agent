"""Unit tests for stage S4 with a fake embedder and the in-memory Qdrant client."""

from pathlib import Path
import warnings

import pytest
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException
from typer.testing import CliRunner

import xn.cli.main as cli
from xn.config import load_config
from xn.embed import EmbedError
from xn.pipeline.runner import RunRequestError, run_stage
from xn.pipeline.s4_embed import STAGE, VERSION, S4RequestError, run_s4
from xn.schemas.extraction import ChapterExtraction
from xn.storage.bm25 import query_vector
from xn.storage.database import connect, init_schema
from xn.storage.qdrant import DENSE_VECTOR, SPARSE_VECTOR, QdrantStore, point_id
from xn.storage.repository import Repository

SIZE = 4
NOVEL = "pntt1"
SUMMARY = "Hàn Lập rời thôn. " * 15
CONFIG = {
    "total_chapters": 3,
    "chunking": {"target_tokens": 100, "overlap_tokens": 10},
    "models": {"embedder": {"name": "fake", "base_url": "http://x", "batch_size": 4}},
    "qdrant": {
        "url": "http://x",
        "collections": {"chunks": "c", "arcs": "a"},
        "dense_size": SIZE,
    },
}


class FakeEmbedder:
    model = "fake"

    def __init__(self, others=(), fail_on=None):
        self.calls = []
        self.others = list(others)
        self.fail_on = fail_on

    def other_loaded_models(self):
        return self.others

    def embed(self, texts):
        self.calls.append(texts)
        if self.fail_on is not None and any(self.fail_on in t for t in texts):
            raise EmbedError("model gone")
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in texts]


class FailingDeleteStore(QdrantStore):
    def delete_chapter(self, *, novel_id, chapter_id):
        raise ResponseHandlingException("qdrant gone")


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
    connection = connect(tmp_path / "s4.db")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn):
    r = Repository(conn)
    r.add_novel(novel_id=NOVEL, title="t", genre_profile="xianxia", total_chapters=3)
    return r


def extraction(chapter_id, summary=SUMMARY):
    return ChapterExtraction(
        chapter_id=chapter_id,
        map_location="x",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        micro_summary=summary,
        events=[],
    ).model_dump_json()


def add_chapter(repo, chapter_id, text, *, s2="done", summary=SUMMARY, novel=NOVEL):
    repo.save_chapter(
        novel_id=novel,
        chapter_id=chapter_id,
        title=None,
        clean_text=text,
        sha256=str(chapter_id),
        token_count=1,
        stage="s0",
        input_hash=str(chapter_id),
    )
    repo.save_extraction(
        novel_id=novel,
        chapter_id=chapter_id,
        payload=extraction(chapter_id, summary),
        prompt_version="v",
        model="m",
        stage="s1",
        input_hash="h",
    )
    if s2:
        repo.upsert_job(novel_id=novel, chapter_id=chapter_id, stage="s2", status=s2)


def add_alias(conn, name, entity_id, first_chapter=1):
    conn.execute(
        "INSERT OR IGNORE INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) "
        "VALUES (?, ?, ?, 'character', ?)",
        (NOVEL, entity_id, name, first_chapter),
    )
    conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) "
        "VALUES (?, ?, ?, ?, 'exact')",
        (NOVEL, name, entity_id, first_chapter),
    )
    conn.commit()


def long_text(word, paragraphs=4):
    return "\n\n".join(" ".join([f"{word}{i}"] * 45) for i in range(paragraphs))


def run(repo, store, embedder, **kwargs):
    return run_s4(repo, novel_id=NOVEL, config=CONFIG, store=store, embedder=embedder, **kwargs)


def all_points(client):
    return client.scroll("c", limit=1000, with_payload=True, with_vectors=True)[0]


def count(client):
    return client.count("c", exact=True).count


def job(repo, chapter_id):
    return repo.get_job(novel_id=NOVEL, chapter_id=chapter_id, stage=STAGE)


def test_writes_text_chunks_and_one_summary_per_chapter(repo, store, client):
    add_chapter(repo, 1, long_text("một"))
    add_chapter(repo, 2, long_text("hai"))
    result = run(repo, store, FakeEmbedder())

    points = all_points(client)
    assert (result.written, result.failed, result.stopped_at) == (2, 0, None)
    assert result.points == len(points)
    for chapter_id in (1, 2):
        kinds = [p.payload["kind"] for p in points if p.payload["chapter_id"] == chapter_id]
        assert kinds.count("summary") == 1
        assert kinds.count("text") > 1


def test_points_carry_both_vectors_and_the_full_payload(repo, store, client, conn):
    add_alias(conn, "hàn lập", 9)
    add_chapter(repo, 1, "Hàn Lập leo núi.")
    conn.execute("UPDATE chapters SET arc_id = 4 WHERE chapter_id = 1")
    conn.commit()
    run(repo, store, FakeEmbedder())

    summary = client.retrieve("c", [point_id(NOVEL, "summary", 1, 0)], with_payload=True, with_vectors=True)[0]
    text = client.retrieve("c", [point_id(NOVEL, "text", 1, 0)], with_payload=True, with_vectors=True)[0]
    assert summary.payload["text"] == SUMMARY
    assert text.payload == {
        "novel_id": NOVEL,
        "chapter_id": 1,
        "chunk_no": 0,
        "arc_id": 4,
        "kind": "text",
        "entity_ids": [9],
        "text": "Hàn Lập leo núi.",
    }
    assert len(text.vector[DENSE_VECTOR]) == SIZE
    assert text.vector[SPARSE_VECTOR].indices


def test_arc_id_is_null_before_s5(repo, store, client):
    add_chapter(repo, 1, "Một đoạn.")
    run(repo, store, FakeEmbedder())
    assert all(p.payload["arc_id"] is None for p in all_points(client))


def test_the_sparse_vector_finds_the_chunk_by_name(repo, store):
    add_chapter(repo, 1, "Hàn Lập leo núi.")
    add_chapter(repo, 2, "Trương Thiết luyện công.")
    run(repo, store, FakeEmbedder())
    indices, values = query_vector("Trương Thiết")
    hits = store.query_chunks(
        novel_id=NOVEL,
        chapter_max=3,
        using=SPARSE_VECTOR,
        query=models.SparseVector(indices=indices, values=values),
        limit=5,
    )
    assert [h.payload["chapter_id"] for h in hits] == [2]


def test_entity_ids_use_only_aliases_visible_at_that_chapter(repo, store, client, conn):
    """A name first seen in chapter 3 tags nothing earlier, even where the words appear."""
    add_alias(conn, "vô danh", 7, first_chapter=3)
    for chapter_id in (1, 2, 3):
        add_chapter(repo, chapter_id, "Vô Danh đứng đó.")
    run(repo, store, FakeEmbedder())

    tags = {
        p.payload["chapter_id"]: p.payload["entity_ids"]
        for p in all_points(client)
        if p.payload["kind"] == "text"
    }
    assert tags == {1: [], 2: [], 3: [7]}


def test_summary_points_are_tagged_too(repo, store, client, conn):
    add_alias(conn, "hàn lập", 9)
    add_chapter(repo, 1, "Không có ai.")
    run(repo, store, FakeEmbedder())
    summary = client.retrieve("c", [point_id(NOVEL, "summary", 1, 0)], with_payload=True)[0]
    assert summary.payload["entity_ids"] == [9]


def test_second_run_makes_no_embed_calls_and_adds_no_points(repo, store, client):
    add_chapter(repo, 1, long_text("một"))
    add_chapter(repo, 2, long_text("hai"))
    run(repo, store, FakeEmbedder())
    before = count(client)

    embedder = FakeEmbedder(others=["busy-model"])
    result = run(repo, store, embedder)
    assert (result.processed, result.written, result.skipped) == (0, 0, 2)
    assert embedder.calls == []
    assert count(client) == before


def test_changed_chapter_rewrites_only_itself_and_leaves_no_stale_points(repo, store, client):
    add_chapter(repo, 1, long_text("một", paragraphs=6))
    add_chapter(repo, 2, long_text("hai"))
    run(repo, store, FakeEmbedder())
    untouched = [p for p in all_points(client) if p.payload["chapter_id"] == 2]

    add_chapter(repo, 1, "Ngắn thôi.")
    embedder = FakeEmbedder()
    result = run(repo, store, embedder)

    assert (result.written, result.skipped) == (1, 1)
    assert len(embedder.calls) == 1
    remaining = [p for p in all_points(client) if p.payload["chapter_id"] == 1]
    assert sorted(p.payload["kind"] for p in remaining) == ["summary", "text"]
    assert [p for p in all_points(client) if p.payload["chapter_id"] == 2] == untouched


def test_a_changed_summary_rewrites_the_chapter(repo, store):
    add_chapter(repo, 1, "Một đoạn.")
    run(repo, store, FakeEmbedder())
    add_chapter(repo, 1, "Một đoạn.", summary="Tóm tắt khác hẳn. " * 15)
    assert run(repo, store, FakeEmbedder()).written == 1


def test_a_changed_alias_rewrites_only_chapters_it_touches(repo, store, client, conn):
    add_chapter(repo, 1, "Hàn Lập leo núi.")
    add_chapter(repo, 2, "Không có ai.", summary="Tóm tắt không tên. " * 15)
    run(repo, store, FakeEmbedder())
    add_alias(conn, "hàn lập", 9)

    result = run(repo, store, FakeEmbedder())
    assert (result.written, result.skipped) == (1, 1)
    assert client.retrieve("c", [point_id(NOVEL, "text", 1, 0)], with_payload=True)[0].payload[
        "entity_ids"
    ] == [9]


def test_a_new_embedding_model_rewrites_the_chapter(repo, store):
    add_chapter(repo, 1, "Một đoạn.")
    run(repo, store, FakeEmbedder())
    embedder = FakeEmbedder()
    embedder.model = "other"
    assert run(repo, store, embedder).written == 1


def test_arc_id_changes_do_not_force_a_re_embed(repo, store, conn):
    add_chapter(repo, 1, "Một đoạn.")
    run(repo, store, FakeEmbedder())
    conn.execute("UPDATE chapters SET arc_id = 4 WHERE chapter_id = 1")
    conn.commit()
    assert run(repo, store, FakeEmbedder()).skipped == 1


def test_a_failed_embed_marks_the_chapter_and_stops_the_run(repo, store, client):
    add_chapter(repo, 1, "Một đoạn.")
    add_chapter(repo, 2, "chương hỏng")
    add_chapter(repo, 3, "Ba đoạn.")
    result = run(repo, store, FakeEmbedder(fail_on="chương hỏng"))

    assert (result.written, result.failed, result.stopped_at) == (1, 1, 2)
    assert job(repo, 2)["status"] == "failed"
    assert "model gone" in job(repo, 2)["error"]
    assert job(repo, 3) is None


def test_a_failed_embed_keeps_the_chapters_old_points(repo, store, client):
    add_chapter(repo, 1, "Một đoạn.")
    run(repo, store, FakeEmbedder())
    before = all_points(client)

    add_chapter(repo, 1, "chương hỏng")
    run(repo, store, FakeEmbedder(fail_on="chương hỏng"))
    assert all_points(client) == before


def test_a_failed_chapter_is_redone_on_the_next_run(repo, store):
    add_chapter(repo, 1, "chương hỏng")
    run(repo, store, FakeEmbedder(fail_on="chương hỏng"))
    result = run(repo, store, FakeEmbedder())
    assert result.written == 1
    assert job(repo, 1)["status"] == "done"
    assert job(repo, 1)["error"] is None


def test_a_qdrant_failure_is_recorded_on_the_chapter(repo, client):
    add_chapter(repo, 1, "Một đoạn.")
    store = FailingDeleteStore.from_config(CONFIG, client)
    result = run(repo, store, FakeEmbedder())
    assert (result.failed, result.stopped_at) == (1, 1)
    assert job(repo, 1)["status"] == "failed"


@pytest.mark.parametrize("status", [None, "failed", "running"])
def test_stops_at_the_first_chapter_where_s2_is_not_done(repo, store, status):
    add_chapter(repo, 1, "Một đoạn.")
    add_chapter(repo, 2, "Hai đoạn.", s2=status)
    add_chapter(repo, 3, "Ba đoạn.")
    result = run(repo, store, FakeEmbedder())
    assert (result.written, result.failed, result.stopped_at) == (1, 0, 2)
    assert job(repo, 2) is None and job(repo, 3) is None


def test_from_and_to_limit_the_chapters(repo, store, client):
    for chapter_id in (1, 2, 3):
        add_chapter(repo, chapter_id, "Một đoạn.")
    run(repo, store, FakeEmbedder(), chapter_from=2, chapter_to=2)
    assert {p.payload["chapter_id"] for p in all_points(client)} == {2}


def test_refuses_to_start_while_another_model_is_loaded(repo, store):
    add_chapter(repo, 1, "Một đoạn.")
    embedder = FakeEmbedder(others=["qwen38-udiq4-hui-10k:latest"])
    with pytest.raises(S4RequestError, match="qwen38-udiq4-hui-10k"):
        run(repo, store, embedder)
    assert embedder.calls == []
    assert job(repo, 1) is None


def test_an_unreachable_ollama_is_a_request_error(repo, store):
    class Down(FakeEmbedder):
        def other_loaded_models(self):
            raise EmbedError("refused")

    add_chapter(repo, 1, "Một đoạn.")
    with pytest.raises(S4RequestError, match="refused"):
        run(repo, store, Down())


def test_an_unreachable_qdrant_is_a_request_error(repo, client):
    class Down(QdrantStore):
        def ensure_collections(self):
            raise ResponseHandlingException("refused")

    add_chapter(repo, 1, "Một đoạn.")
    with pytest.raises(S4RequestError, match="refused"):
        run(repo, Down.from_config(CONFIG, client), FakeEmbedder())


def test_job_records_the_version_and_model(repo, store):
    add_chapter(repo, 1, "Một đoạn.")
    run(repo, store, FakeEmbedder())
    assert (job(repo, 1)["prompt_version"], job(repo, 1)["model"]) == (VERSION, "fake")


def test_points_stay_within_their_novel(repo, store, client):
    repo.add_novel(novel_id="other", title="t", genre_profile="xianxia", total_chapters=3)
    add_chapter(repo, 1, "Một đoạn.")
    add_chapter(repo, 1, "Của truyện khác.", novel="other")
    run(repo, store, FakeEmbedder())
    assert {p.payload["novel_id"] for p in all_points(client)} == {NOVEL}


def test_delete_chapter_leaves_other_chapters_and_novels(repo, store, client):
    repo.add_novel(novel_id="other", title="t", genre_profile="xianxia", total_chapters=3)
    add_chapter(repo, 1, "Một đoạn.")
    add_chapter(repo, 2, "Hai đoạn.")
    add_chapter(repo, 1, "Của truyện khác.", novel="other")
    run(repo, store, FakeEmbedder())
    run_s4(repo, novel_id="other", config=CONFIG, store=store, embedder=FakeEmbedder())

    store.delete_chapter(novel_id=NOVEL, chapter_id=1)
    left = {(p.payload["novel_id"], p.payload["chapter_id"]) for p in all_points(client)}
    assert left == {(NOVEL, 2), ("other", 1)}


def test_delete_chapter_requires_a_novel_id(store):
    with pytest.raises(ValueError):
        store.delete_chapter(novel_id="", chapter_id=1)


def test_runner_passes_the_stage_through(repo, store, client):
    add_chapter(repo, 1, "Một đoạn.")
    result = run_stage(
        repo, stage="s4", novel_id=NOVEL, config=CONFIG, store=store, embedder=FakeEmbedder()
    )
    assert result.written == 1
    assert count(client) == 2


def test_runner_refuses_stale_only_on_s4(repo, store):
    with pytest.raises(RunRequestError, match="stale_only"):
        run_stage(repo, stage="s4", novel_id=NOVEL, config=CONFIG, stale_only=True)


def test_runner_refuses_rebuild_on_s4(repo):
    with pytest.raises(RunRequestError, match="rebuild"):
        run_stage(repo, stage="s4", novel_id=NOVEL, config=CONFIG, rebuild=True)


def test_real_config_sets_the_agreed_chunking_and_embedder():
    config = load_config(NOVEL, configs_dir=Path(__file__).resolve().parents[2] / "configs")
    assert config["chunking"] == {"target_tokens": 450, "overlap_tokens": 60}
    assert config["models"]["embedder"]["name"] == "bge-m3:latest"


class TestCli:
    @pytest.fixture
    def db(self, tmp_path):
        path = tmp_path / "cli.db"
        connection = connect(path)
        init_schema(connection)
        repo = Repository(connection)
        repo.add_novel(novel_id=NOVEL, title="t", genre_profile="xianxia", total_chapters=3)
        add_chapter(repo, 1, "Một đoạn.")
        add_chapter(repo, 2, "Hai đoạn.")
        connection.close()
        return path

    @staticmethod
    def invoke(db: Path, *extra: str):
        args = ["run", "--novel", NOVEL, "--stage", "s4", "--db", str(db), *extra]
        return CliRunner().invoke(cli.app, args)

    @staticmethod
    def wire(monkeypatch, store, embedder):
        monkeypatch.setattr(cli, "load_config", lambda novel, configs_dir: CONFIG)
        monkeypatch.setattr("xn.pipeline.runner.QdrantStore.from_config", lambda config: store)
        monkeypatch.setattr("xn.pipeline.runner.OllamaEmbedder.from_config", lambda config: embedder)

    def test_from_and_to_run_and_report(self, db, store, client, monkeypatch):
        self.wire(monkeypatch, store, FakeEmbedder())
        result = self.invoke(db, "--from", "2", "--to", "2")
        assert result.exit_code == 0
        assert "s4: processed=1 written=1 skipped=0 failed=0 points=2" in result.output
        assert {p.payload["chapter_id"] for p in all_points(client)} == {2}

    def test_a_loaded_model_prints_the_reason_and_exits_1(self, db, store, monkeypatch):
        self.wire(monkeypatch, store, FakeEmbedder(others=["busy"]))
        result = self.invoke(db)
        assert result.exit_code == 1
        assert "unload these Ollama models before S4: busy" in result.output

    def test_a_failed_chapter_exits_1(self, db, store, monkeypatch):
        self.wire(monkeypatch, store, FakeEmbedder(fail_on="Hai đoạn"))
        result = self.invoke(db)
        assert result.exit_code == 1
        assert "stopped_at=2" in result.output
