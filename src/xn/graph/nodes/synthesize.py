"""Synthesize node: answer from the evidence pack on an OpenRouter model."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent

from xn.graph.deps import GraphDeps, prompt_version
from xn.llm import CompactItem, run_compact
from xn.prompts import load_prompt, profile_from_config
from xn.schemas.state import Answer, QueryState

MODEL_KEYS = {
    "timeline": "macro_synthesizer",
    "factual_qa": "macro_synthesizer",
    "deep_dive": "deep_dive_evaluator",
    "compare": "deep_dive_evaluator",
}


def synthesize(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Produce an Answer through the compact egress path."""
    prompt = load_prompt(
        "synthesize_compact",
        prompt_version(deps, "synthesize_compact"),
        profile_from_config(deps.config),
        deps.prompts_dir,
    )
    model_cfg = deps.config["models"][MODEL_KEYS[state["decision"].intent]]
    items = [
        CompactItem(kind="event", chapter_id=item.chapter_id, text=item.cloud_text())
        for item in sorted(state["evidence"].items, key=lambda i: (i.chapter_id, i.event_id))
    ]
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
    )
    return {"answer": result.output}
