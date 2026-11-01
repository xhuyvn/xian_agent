"""Route node: read intent and entity mentions from the question with the local model."""

from __future__ import annotations

from html import escape
import logging
from typing import Any

from pydantic_ai import Agent

from xn.graph.deps import GraphDeps, prompt_version
from xn.llm import extractor_output, extractor_settings, local_model, run_logged
from xn.prompts import load_prompt, profile_from_config
from xn.schemas.state import QueryState, RouterDecision

logger = logging.getLogger(__name__)

# Values a follow-up must not inherit from the turn before it.
TURN_RESET: dict[str, Any] = {
    "entity_ids": [],
    "unresolved_mentions": [],
    "suggestions": {},
    "evidence": None,
    "answer": None,
    "grounding_ratio": None,
    "refusal": None,
}


def _decide(state: QueryState, deps: GraphDeps) -> RouterDecision:
    """Ask the local model for a RouterDecision, writing one llm_runs row."""
    model = deps.router_model or local_model(deps.config)
    prompt = load_prompt(
        "router",
        prompt_version(deps, "router"),
        profile_from_config(deps.config),
        deps.prompts_dir,
    )
    agent = Agent(
        model,
        output_type=extractor_output(RouterDecision, deps.config),
        system_prompt=prompt.text,
        retries=3,
        model_settings=extractor_settings(deps.config),
    )
    result = run_logged(
        agent,
        f"<question>{escape(state['query'], quote=False)}</question>",
        repo=deps.repo,
        novel_id=state["novel_id"],
        stage="route",
        chapter_id=None,
        model=deps.config["models"]["local_extractor"]["serving_name"],
        prompt_version=prompt.prompt_version,
    )
    return result.output


def route(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Classify the question; on any failure fall back to factual_qa with no mentions."""
    try:
        return {**TURN_RESET, "decision": _decide(state, deps)}
    except Exception as err:
        logger.warning("Router failed, using factual_qa: %s", err)
        return {
            **TURN_RESET,
            "decision": RouterDecision(intent="factual_qa", mentions=[]),
            "errors": [f"route: {err}"],
        }
