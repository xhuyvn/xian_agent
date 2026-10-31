"""LLM factory and execution logging layer for XN-Agent."""

from __future__ import annotations

import logging
import time
from typing import Any
import uuid

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[tuple[str, str], OpenAIChatModel] = {}


def local_model(config: dict[str, Any]) -> OpenAIChatModel:
    """Build or retrieve cached Pydantic-AI OpenAIChatModel for local extraction.

    Constructs the model on first call from models.local_extractor.serving_name
    and base_url with api_key="ollama", and caches per (serving_name, base_url).

    Raises:
        ValueError: If models.local_extractor.serving_name is missing.
    """
    models_cfg = config.get("models", {}).get("local_extractor", {})
    serving_name = models_cfg.get("serving_name")
    if not serving_name:
        raise ValueError("Missing required config key: models.local_extractor.serving_name")
    base_url = models_cfg.get("base_url") or "http://localhost:11434/v1"

    cache_key = (serving_name, base_url)
    if cache_key not in _MODEL_CACHE:
        provider = OpenAIProvider(base_url=base_url, api_key="ollama")
        _MODEL_CACHE[cache_key] = OpenAIChatModel(serving_name, provider=provider)
    return _MODEL_CACHE[cache_key]


def run_logged(
    agent: Any,
    user_prompt: str,
    *,
    repo: Any,
    novel_id: str,
    stage: str,
    chapter_id: int | None,
    model: str,
    prompt_version: str,
) -> Any:
    """Run agent.run_sync(user_prompt) and write an llm_runs record.

    Writes exactly one row to llm_runs for the call.
    On exception from agent.run_sync, writes one row with ok=0, null tokens and null retries,
    then re-raises.
    A failure in logging an error must not hide the original exception.
    """
    start_time = time.perf_counter()
    run_id = uuid.uuid4().hex

    try:
        result = agent.run_sync(user_prompt)
    except Exception:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        try:
            repo.add_llm_run(
                novel_id=novel_id,
                run_id=run_id,
                stage=stage,
                chapter_id=chapter_id,
                model=model,
                prompt_version=prompt_version,
                input_tokens=None,
                output_tokens=None,
                latency_ms=latency_ms,
                retries=None,
                ok=0,
                cost_usd=0.0,
                egress_class="local",
                payload_chapters=str(chapter_id) if chapter_id is not None else None,
            )
        except Exception as log_err:
            logger.warning("Failed to record failed LLM run to repository: %s", log_err)
        raise

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    usage = result.usage
    in_tokens = usage.input_tokens
    out_tokens = usage.output_tokens
    retries = max(usage.requests - 1, 0)

    repo.add_llm_run(
        novel_id=novel_id,
        run_id=run_id,
        stage=stage,
        chapter_id=chapter_id,
        model=model,
        prompt_version=prompt_version,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        latency_ms=latency_ms,
        retries=retries,
        ok=1,
        cost_usd=0.0,
        egress_class="local",
        payload_chapters=str(chapter_id) if chapter_id is not None else None,
    )
    return result
