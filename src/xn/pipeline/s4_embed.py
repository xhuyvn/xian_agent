"""Stage S4: paragraph chunks and micro-summaries, embedded into `xn_chunks`."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import re
import textwrap
from typing import Any, Protocol

from qdrant_client.http.exceptions import ApiException

from xn.embed import EmbedError
from xn.schemas.extraction import ChapterExtraction
from xn.storage.bm25 import document_vector, tokenize
from xn.storage.qdrant import ChunkPoint, QdrantStore
from xn.storage.repository import Repository
from xn.text import CHARS_PER_TOKEN, estimate_tokens

logger = logging.getLogger(__name__)

STAGE = "s4"
S2_STAGE = "s2"
# Stored as the job's prompt_version. Bump it when chunking or the BM25 weights change.
VERSION = "s4-v1"

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")
_JOIN = "\n\n"


def _units(text: str, target: int) -> Iterator[str]:
    """Yield paragraphs, falling back to sentences, then words, for anything over target."""
    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if estimate_tokens(paragraph) <= target:
            yield paragraph
            continue
        for sentence in _SENTENCE_BREAK.split(paragraph):
            if estimate_tokens(sentence) <= target:
                yield sentence
            else:
                yield from textwrap.wrap(sentence, width=int(target * CHARS_PER_TOKEN))


def _tail(text: str, overlap: int) -> str:
    """Return the last whole words of text that fit in the overlap."""
    tail = ""
    for word in reversed(text.split()):
        candidate = f"{word} {tail}".rstrip()
        if estimate_tokens(candidate) > overlap:
            break
        tail = candidate
    return tail


def split_chunks(text: str, *, target: int, overlap: int) -> list[str]:
    """Pack paragraphs into chunks of at most target tokens, each opening with the last one's tail."""
    chunks: list[str] = []
    current: list[str] = []
    for unit in _units(text, target):
        if current and estimate_tokens(_JOIN.join([*current, unit])) > target:
            chunks.append(_JOIN.join(current))
            tail = _tail(chunks[-1], overlap)
            current = [tail] if tail and estimate_tokens(tail + _JOIN + unit) <= target else []
        current.append(unit)
    if current:
        chunks.append(_JOIN.join(current))
    return chunks


class AliasIndex:
    """Whole-word alias lookup that sees only the aliases visible at a chapter."""

    def __init__(self, rows: Iterable[Mapping[str, object]]) -> None:
        self._aliases: dict[tuple[str, ...], list[tuple[int, int]]] = defaultdict(list)
        for row in rows:
            words = tuple(tokenize(str(row["alias_norm"])))
            if words:
                self._aliases[words].append((int(row["first_chapter"]), int(row["entity_id"])))
        self._lengths = sorted({len(words) for words in self._aliases})

    def entities_in(self, text: str, *, chapter_id: int) -> list[int]:
        """Return the sorted IDs of every entity with an alias in text, as of chapter_id."""
        words = tokenize(text)
        found: set[int] = set()
        for length in self._lengths:
            for start in range(len(words) - length + 1):
                for first_chapter, entity_id in self._aliases.get(
                    tuple(words[start : start + length]), ()
                ):
                    if first_chapter <= chapter_id:
                        found.add(entity_id)
        return sorted(found)


class Embedder(Protocol):
    """The part of OllamaEmbedder that S4 uses."""

    model: str

    def other_loaded_models(self) -> list[str]: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class S4RequestError(RuntimeError):
    """Raised before any chapter is touched, when S4 cannot start."""


@dataclass(frozen=True)
class S4Result:
    """Result metrics for an S4 pipeline run."""

    processed: int
    written: int
    skipped: int
    failed: int
    points: int
    stopped_at: int | None = None


@dataclass(frozen=True)
class ChapterItem:
    """One text to embed, before it has vectors."""

    kind: str
    chunk_no: int
    text: str
    entity_ids: list[int]


def build_items(
    clean_text: str,
    micro_summary: str,
    *,
    chapter_id: int,
    aliases: AliasIndex,
    target: int,
    overlap: int,
) -> list[ChapterItem]:
    """Return a chapter's text chunks followed by its summary."""
    chunks = split_chunks(clean_text, target=target, overlap=overlap)
    texts = [("text", i, chunk) for i, chunk in enumerate(chunks)]
    texts.append(("summary", 0, micro_summary))
    return [
        ChapterItem(kind, chunk_no, text, aliases.entities_in(text, chapter_id=chapter_id))
        for kind, chunk_no, text in texts
    ]


def compute_input_hash(items: list[ChapterItem], *, model: str) -> str:
    """Hash what the chapter's points depend on. `arc_id` is left out, because S5 sets it in place."""
    payload = {"version": VERSION, "model": model, "items": [asdict(item) for item in items]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _prepare(store: QdrantStore, embedder: Embedder) -> None:
    """Check Ollama holds no other model and the collections exist."""
    try:
        others = embedder.other_loaded_models()
    except EmbedError as err:
        raise S4RequestError(str(err)) from err
    if others:
        raise S4RequestError(f"unload these Ollama models before S4: {', '.join(others)}")
    try:
        store.ensure_collections()
    except (ApiException, ValueError) as err:
        raise S4RequestError(f"Qdrant is not ready: {err}") from err


def _embed_points(
    items: list[ChapterItem], *, chapter_id: int, arc_id: int | None, embedder: Embedder
) -> list[ChunkPoint]:
    dense = embedder.embed([item.text for item in items])
    points = []
    for item, vector in zip(items, dense, strict=True):
        indices, values = document_vector(item.text)
        points.append(
            ChunkPoint(
                kind=item.kind,
                chapter_id=chapter_id,
                chunk_no=item.chunk_no,
                arc_id=arc_id,
                entity_ids=item.entity_ids,
                text=item.text,
                dense=vector,
                sparse_indices=indices,
                sparse_values=values,
            )
        )
    return points


def run_s4(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    store: QdrantStore,
    embedder: Embedder,
    chapter_from: int = 1,
    chapter_to: int | None = None,
) -> S4Result:
    """Run stage S4. Unchanged chapters touch neither Ollama nor Qdrant."""
    if chapter_to is None:
        chapter_to = config["total_chapters"]
    target = config["chunking"]["target_tokens"]
    overlap = config["chunking"]["overlap_tokens"]

    chapters = repo.list_chapters(novel_id=novel_id, chapter_max=chapter_to)
    extractions = repo.list_extractions(novel_id=novel_id, chapter_max=chapter_to)
    extractions_by_id = {row["chapter_id"]: row for row in extractions}
    aliases = AliasIndex(repo.list_aliases(novel_id=novel_id, chapter_max=chapter_to))

    processed = 0
    written = 0
    skipped = 0
    failed = 0
    points_written = 0
    stopped_at: int | None = None
    prepared = False

    for ch in chapters:
        cid = ch["chapter_id"]
        if cid < chapter_from:
            continue

        s2_job = repo.get_job(novel_id=novel_id, chapter_id=cid, stage=S2_STAGE)
        if s2_job is None or s2_job["status"] != "done" or cid not in extractions_by_id:
            logger.warning("Chapter %d missing completed S2 job or extraction row; stopping S4", cid)
            stopped_at = cid
            break

        extraction = ChapterExtraction.model_validate_json(extractions_by_id[cid]["payload"])
        items = build_items(
            ch["clean_text"],
            extraction.micro_summary,
            chapter_id=cid,
            aliases=aliases,
            target=target,
            overlap=overlap,
        )
        input_hash = compute_input_hash(items, model=embedder.model)

        job = repo.get_job(novel_id=novel_id, chapter_id=cid, stage=STAGE)
        if job is not None and job["status"] == "done" and job["input_hash"] == input_hash:
            skipped += 1
            continue

        if not prepared:
            _prepare(store, embedder)
            prepared = True

        processed += 1
        job_fields: dict[str, Any] = {
            "novel_id": novel_id,
            "chapter_id": cid,
            "stage": STAGE,
            "input_hash": input_hash,
            "prompt_version": VERSION,
            "model": embedder.model,
        }
        repo.upsert_job(status="running", **job_fields)

        try:
            points = _embed_points(items, chapter_id=cid, arc_id=ch["arc_id"], embedder=embedder)
            store.delete_chapter(novel_id=novel_id, chapter_id=cid)
            store.upsert_chunks(novel_id=novel_id, points=points)
            repo.upsert_job(status="done", **job_fields)
            written += 1
            points_written += len(points)
        except Exception as err:
            logger.error("Chapter %d embedding failed: %s", cid, err)
            repo.upsert_job(status="failed", error=str(err), **job_fields)
            failed += 1
            stopped_at = cid
            break

    return S4Result(
        processed=processed,
        written=written,
        skipped=skipped,
        failed=failed,
        points=points_written,
        stopped_at=stopped_at,
    )
