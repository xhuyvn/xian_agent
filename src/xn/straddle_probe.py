"""Arc-straddling spoiler probes for the query graph (G-28, blueprint Sprint 3 criteria 2-3).

Evidence probes build the retrieve node's pack, with no cloud call, at several N inside each
arc and check that nothing in it comes from after N. Answer probes run whole `xn ask` turns.

    py -m xn.straddle_probe evidence --db data/eval/g28-t5.db --out probe.json
    py -m xn.straddle_probe answers --db data/eval/g28-t5.db --out answers.json
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import sys
import time
from typing import Any, Optional
import uuid

from xn.config import load_config
from xn.graph.deps import GraphDeps
from xn.graph.nodes.retrieve import retrieve
from xn.graph.nodes.spoiler_scan import REDACTED, find_leaks, forbidden_terms, realm_ladder
from xn.graph.nodes.synthesize import compact_items
from xn.graph.workflow import ask, build_graph, open_checkpointer
from xn.llm import MAX_EVIDENCE_TOKENS, validate_compact
from xn.schemas.arc import StoryArc
from xn.schemas.state import EvidencePack, RouterDecision, SummaryEvidence
from xn.storage.database import connect
from xn.storage.repository import Repository
from xn.text import normalize_text

logger = logging.getLogger(__name__)

PROTAGONIST = 1
TIMELINE_QUERY = "Tóm tắt các diễn biến chính của câu chuyện cho đến nay theo dòng thời gian."
DEEP_DIVE_QUERY = "Phân tích chi tiết nhân vật Hàn Lập: tu vi, bảo vật và những sự kiện quan trọng."
# N inside six arcs for the answer probes: at, between and before checkpoints.
ANSWER_NS = (75, 157, 263, 385, 477, 585)
MIN_ANSWER_CHARS = 300
_HEADER = re.compile(r"^(?:.*?chương \d+–\d+[^:]*|Chương \d+): (?:\(phần \d+/\d+\) )?", re.DOTALL)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _exact_words(text: str) -> str:
    """Casefolded words with their diacritics kept, padded for word-bound matching.

    The L5 scan folds diacritics away, so "đại phu nhận" matches the alias "đại phu nhân";
    a probe hit counts only when the term also matches with its diacritics.
    """
    return f" {' '.join(re.findall(r'\w+', normalize_text(text).casefold()))} "


@dataclass(frozen=True)
class Arc:
    arc_id: int
    start: int
    end: int
    summary: str
    resolution: str


def load_arcs(conn: Any, novel_id: str) -> list[Arc]:
    """Read every arc directly; the probe is the judge and may see past N."""
    arcs = []
    for row in conn.execute(
        "SELECT arc_id, start_chapter, end_chapter, payload FROM arcs WHERE novel_id = ? ORDER BY arc_id",
        (novel_id,),
    ):
        payload = StoryArc.model_validate_json(row["payload"])
        arcs.append(Arc(row["arc_id"], row["start_chapter"], row["end_chapter"], payload.summary, payload.resolution))
    return arcs


def probe_ns(arc: Arc) -> list[int]:
    """Start, first checkpoint, checkpoint + 3, middle, end - 1 and the end (as a control)."""
    first = next((c for c in range(arc.start + 1, arc.end + 1) if c % 10 == 0), arc.end)
    ns = {arc.start, first, first + 3, (arc.start + arc.end) // 2, arc.end - 1, arc.end}
    return sorted(n for n in ns if arc.start <= n <= arc.end)


class Sources:
    """Every summary text by the last chapter it draws on, read without a bound."""

    def __init__(self, conn: Any, novel_id: str, arcs: list[Arc]) -> None:
        self.arcs = {a.arc_id: a for a in arcs}
        self.prefix = {
            (r["arc_id"], r["upto_chapter"]): _norm(r["summary"])
            for r in conn.execute("SELECT * FROM arc_prefix_summaries WHERE novel_id = ?", (novel_id,))
        }
        self.digests = {
            (r["entity_id"], r["arc_id"], r["upto_chapter"]): _norm(r["digest"])
            for r in conn.execute("SELECT * FROM entity_arc_digests WHERE novel_id = ?", (novel_id,))
        }
        self.micro = {
            r["chapter_id"]: _norm(r["m"] or "")
            for r in conn.execute(
                "SELECT chapter_id, json_extract(payload, '$.micro_summary') AS m FROM extractions WHERE novel_id = ?",
                (novel_id,),
            )
        }

    def allowed(self, item: SummaryEvidence, n: int) -> list[str]:
        """Source texts this item may come from: right kind, right arc or entity, ending <= N."""
        if item.kind == "arc_summary":
            arc = self.arcs[item.arc_id]
            return [_norm(arc.summary), _norm(arc.resolution)] if arc.end <= n else []
        if item.kind == "arc_prefix":
            return [t for (a, upto), t in self.prefix.items() if a == item.arc_id and upto <= n]
        if item.kind == "digest":
            return [
                t for (e, a, upto), t in self.digests.items()
                if e == item.entity_id and a == item.arc_id and upto <= n
            ]
        return [self.micro.get(item.chapter_id, "")] if item.chapter_id <= n else []


def check_pack(pack: EvidencePack, n: int, sources: Sources) -> list[str]:
    """Return every hard rule the pack breaks at N: chapter bound, source, egress, budget."""
    problems = []
    for chapter in [i.chapter_id for i in pack.items] + [c.chapter_id for c in pack.chunks]:
        if chapter > n:
            problems.append(f"event or chunk from chapter {chapter}")
    for item in pack.summaries:
        if item.chapter_id > n:
            problems.append(f"{item.kind} cites chapter {item.chapter_id}")
        body = _norm(_HEADER.sub("", item.text, count=1))
        if not any(body in source for source in sources.allowed(item, n)):
            problems.append(f"{item.kind} of arc {item.arc_id} not found in any source ending <= N: {body[:60]}")
    try:
        validate_compact(compact_items(pack), n)
    except Exception as err:
        problems.append(f"egress: {err}")
    if pack.tokens > MAX_EVIDENCE_TOKENS:
        problems.append(f"{pack.tokens} tokens")
    return problems


def terms_to_review(pack: EvidencePack, n: int, terms: list[str], raw: dict[int, str]) -> list[str]:
    """Future aliases or realms that the pack names exactly but the novel does not name by N.

    Such a term is not proof of a leak (a summary may word a thing the way a later chapter
    names it), so each one is read by hand.
    """
    text = _exact_words(
        "\n".join([s.text for s in pack.summaries] + [i.cloud_text() for i in pack.items] + [c.text for c in pack.chunks])
    )
    review = []
    for term in find_leaks(text, terms):
        exact = _exact_words(term)
        if exact in text and not any(exact in raw[c] for c in raw if c <= n):
            review.append(term)
    return review


def other_entity(conn: Any, novel_id: str, arc: Arc, n: int) -> Optional[int]:
    """The non-protagonist with the most digests in the arc that a reader at N already knows."""
    row = conn.execute(
        "SELECT d.entity_id FROM entity_arc_digests d JOIN entities e "
        "ON e.novel_id = d.novel_id AND e.entity_id = d.entity_id "
        "WHERE d.novel_id = ? AND d.arc_id = ? AND d.entity_id != ? AND e.first_chapter <= ? "
        "GROUP BY d.entity_id ORDER BY COUNT(*) DESC, d.entity_id LIMIT 1",
        (novel_id, arc.arc_id, PROTAGONIST, n),
    ).fetchone()
    return row[0] if row else None


def run_evidence(db: Path, novel_id: str, configs_dir: Path) -> dict[str, Any]:
    """Build the timeline and deep-dive packs at every probe N and check each one."""
    config = load_config(novel_id, configs_dir=configs_dir)
    conn = connect(db)
    repo = Repository(conn)
    deps = GraphDeps(repo=repo, config=config)
    arcs = load_arcs(conn, novel_id)
    sources = Sources(conn, novel_id, arcs)
    ladder = realm_ladder(config)
    raw = {
        row["chapter_id"]: _exact_words(row["clean_text"])
        for row in conn.execute("SELECT chapter_id, clean_text FROM chapters WHERE novel_id = ?", (novel_id,))
    }
    results = []
    for arc in arcs:
        for n in probe_ns(arc):
            terms = forbidden_terms(repo, novel_id=novel_id, chapter_max=n, ladder=ladder)
            probes = [("timeline", None, TIMELINE_QUERY), ("deep_dive", PROTAGONIST, DEEP_DIVE_QUERY)]
            other = other_entity(conn, novel_id, arc, n)
            if other is not None:
                probes.append(("deep_dive", other, "Phân tích chi tiết thực thể này."))
            for intent, entity_id, query in probes:
                state = {
                    "novel_id": novel_id,
                    "chapter_max": n,
                    "query": query,
                    "entity_ids": [entity_id] if entity_id else [],
                    "decision": RouterDecision(intent=intent),
                }
                out = retrieve(state, deps)
                pack = out["evidence"]
                kinds = [s.kind for s in pack.summaries]
                result = {
                    "arc": arc.arc_id,
                    "n": n,
                    "intent": intent,
                    "entity_id": entity_id,
                    "tokens": pack.tokens,
                    "events": len(pack.items),
                    "chunks": len(pack.chunks),
                    "summaries": {k: kinds.count(k) for k in sorted(set(kinds))},
                    "errors": out.get("errors", []),
                    "problems": check_pack(pack, n, sources),
                    "terms_to_review": terms_to_review(pack, n, terms, raw),
                }
                if n == arc.end:
                    # Control: at the arc's end its full summary and full digest must be in.
                    ids = {(s.kind, s.arc_id, s.chapter_id) for s in pack.summaries}
                    want = ("arc_summary" if intent == "timeline" else "digest", arc.arc_id, arc.end)
                    if want not in ids and (intent == "timeline" or entity_id == PROTAGONIST):
                        result["problems"].append(f"control: {want} missing at the arc end")
                results.append(result)
                print(
                    f"arc {arc.arc_id:2d} N={n:3d} {intent:9s} e={entity_id} tokens={pack.tokens:5d} "
                    f"problems={len(result['problems'])} review={result['terms_to_review']}",
                    flush=True,
                )
    conn.close()
    protagonist = [r["tokens"] for r in results if r["entity_id"] == PROTAGONIST]
    return {
        "packs": len(results),
        "packs_with_problems": sum(bool(r["problems"]) for r in results),
        "packs_with_terms_to_review": sum(bool(r["terms_to_review"]) for r in results),
        "max_protagonist_tokens": max(protagonist, default=0),
        "max_tokens": max((r["tokens"] for r in results), default=0),
        "results": results,
    }


class _Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def run_answers(db: Path, novel_id: str, configs_dir: Path, prompts_dir: Path, checkpoints: Path) -> dict[str, Any]:
    """Run 6 timeline and 6 protagonist deep-dive turns through the whole graph."""
    config = load_config(novel_id, configs_dir=configs_dir)
    conn = connect(db)
    repo = Repository(conn)
    saver = open_checkpointer(checkpoints)
    graph = build_graph(GraphDeps(repo=repo, config=config, prompts_dir=prompts_dir), saver)
    ladder = realm_ladder(config)
    handler = _Warnings()
    logging.getLogger("xn").addHandler(handler)
    results = []
    for n in ANSWER_NS:
        for query in (TIMELINE_QUERY, DEEP_DIVE_QUERY):
            handler.messages.clear()
            start = time.perf_counter()
            try:
                state = ask(graph, thread_id=uuid.uuid4().hex, novel_id=novel_id, query=query, until=n)
            except Exception as err:
                # A failed turn sends nothing to the reader, so it cannot leak; it is recorded.
                results.append({"n": n, "query": query, "failure": f"{type(err).__name__}: {err}"[:300],
                                "seconds": round(time.perf_counter() - start, 1)})
                print(f"N={n} FAILED {results[-1]['failure']}", flush=True)
                continue
            evidence = state.get("evidence")
            answer = state.get("answer")
            markdown = answer.markdown if answer else ""
            terms = forbidden_terms(repo, novel_id=novel_id, chapter_max=n, ladder=ladder, evidence=evidence)
            result = {
                "n": n,
                "query": query,
                "intent": state["decision"].intent,
                "chapter_max": state.get("chapter_max"),
                "tokens": evidence.tokens if evidence else None,
                "citations": answer.citations if answer else [],
                "citations_after_n": [c for c in (answer.citations if answer else []) if c > n],
                "spoiler_retries": state.get("spoiler_retries", 0),
                "redacted": markdown.count(REDACTED),
                "future_terms_in_answer": find_leaks(markdown, terms),
                "warnings": list(handler.messages),
                "refusal": state.get("refusal"),
                "errors": state.get("errors", []),
                "grounding_ratio": state.get("grounding_ratio"),
                "seconds": round(time.perf_counter() - start, 1),
                # A stub or an uncited answer is a failure too, not a pass with nothing to check.
                "usable": len(markdown) >= MIN_ANSWER_CHARS and bool(answer and answer.citations),
                "markdown": markdown,
            }
            results.append(result)
            print(
                f"N={n} {result['intent']:9s} tokens={result['tokens']} cites>N={result['citations_after_n']} "
                f"retries={result['spoiler_retries']} terms={result['future_terms_in_answer']} "
                f"refusal={result['refusal']} usable={result['usable']} {result['seconds']}s",
                flush=True,
            )
    conn.close()
    saver.conn.close()
    usable = sum(bool(r.get("usable")) for r in results)
    print(f"answers={len(results)} usable={usable}")
    return {"answers": len(results), "usable": usable, "results": results}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["evidence", "answers"])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--novel", default="pntt1")
    parser.add_argument("--configs-dir", type=Path, default=Path("configs"))
    parser.add_argument("--prompts-dir", type=Path, default=Path("prompts"))
    parser.add_argument("--checkpoints", type=Path, default=Path("data/eval/g28-probe-checkpoints.db"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.kind == "evidence":
        report = run_evidence(args.db, args.novel, args.configs_dir)
        print(
            f"packs={report['packs']} with problems={report['packs_with_problems']} "
            f"with terms to review={report['packs_with_terms_to_review']} "
            f"max protagonist tokens={report['max_protagonist_tokens']} max tokens={report['max_tokens']}"
        )
    else:
        report = run_answers(args.db, args.novel, args.configs_dir, args.prompts_dir, args.checkpoints)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
