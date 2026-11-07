"""Synthesize node: answer from the evidence pack (summaries, events, chunks) on an OpenRouter model."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent

from xn.graph.deps import GraphDeps, prompt_version
from xn.llm import CompactItem, run_compact
from xn.prompts import load_prompt, profile_from_config
from xn.schemas.state import Answer, EvidencePack, QueryState

# Compact egress kind of each summary kind; the current arc's prefix summary is an arc summary.
SUMMARY_KINDS = {
    "arc_summary": "arc_summary",
    "arc_prefix": "arc_summary",
    "micro_summary": "micro_summary",
    "digest": "digest",
}

# The model reasons before it answers, and on G-28's larger packs 2500 tokens ran out before
# any answer (11 of 12 live probes); S6 uses the same cap for the same reason (owner, G-28).
MAX_OUTPUT_TOKENS = 8000

MODEL_KEYS = {
    "timeline": "macro_synthesizer",
    "factual_qa": "macro_synthesizer",
    "deep_dive": "deep_dive_evaluator",
    "compare": "deep_dive_evaluator",
}


def compact_items(evidence: EvidencePack) -> list[CompactItem]:
    """Turn the pack into compact egress items: summaries, then events, then chunks.

    Summaries come in story order, so the model reads the overview before the details;
    chunks keep their rerank order, so the best passage comes first.
    """
    items = [
        CompactItem(kind=SUMMARY_KINDS[s.kind], chapter_id=s.chapter_id, text=s.text)
        for s in sorted(evidence.summaries, key=lambda s: (s.start_chapter, s.chapter_id, s.entity_id or 0))
    ]
    items += [
        CompactItem(kind="event", chapter_id=item.chapter_id, text=item.cloud_text())
        for item in sorted(evidence.items, key=lambda i: (i.chapter_id, i.event_id))
    ]
    items += [CompactItem(kind="chunk", chapter_id=c.chapter_id, text=c.text) for c in evidence.chunks]
    return items


def synthesize(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Produce an Answer through the compact egress path, avoiding any forbidden terms."""
    # On an L5 retry the prompt lists the leaked terms; the first pass lists none.
    profile = {**profile_from_config(deps.config), "forbidden": state.get("forbidden") or []}
    prompt = load_prompt(
        "synthesize_compact", prompt_version(deps, "synthesize_compact"), profile, deps.prompts_dir
    )
    model_cfg = deps.config["models"][MODEL_KEYS[state["decision"].intent]]
    items = compact_items(state["evidence"])
    agent = Agent(output_type=Answer, system_prompt=prompt.text, retries=1)
    result = run_compact(
        agent,
        state["query"],
        items,
        repo=deps.repo,
        novel_id=state["novel_id"],
        stage="synthesize",
        chapter_max=state["chapter_max"],
        model_name=model_cfg["name"],
        prompt_version=prompt.prompt_version,
        config=deps.config,
        temperature=model_cfg.get("temperature", 0.3),
        model_factory=deps.cloud_factory,
        sleep=deps.sleep,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return {"answer": result.output}
