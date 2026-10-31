"""LLM factory and execution logging layer for XN-Agent."""

from __future__ import annotations

import time
from typing import Any
import uuid

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

_MODEL_CACHE: dict[tuple[str, str], OpenAIChatModel] = {}


def local_model(config: dict[str, Any]) -> OpenAIChatModel:
    """Build or retrieve cached Pydantic-AI OpenAIChatModel for local extraction.

    Constructs the model on first call from models.local_extractor.serving_name
    and base_url with api_key="ollama", and caches per (serving_name, base_url).
    """
    models_cfg = config.get("models", {}).get("local_extractor", {})
    serving_name = models_cfg.get("serving_name") or models_cfg.get("name") or "qwen14b-12k"
    base_url = models_cfg.get("base_url") or "http://localhost:11434/v1"

    cache_key = (serving_name, base_url)
    if cache_key not in _MODEL_CACHE:
        provider = OpenAIProvider(base_url=base_url, api_key="ollama")
        _MODEL_CACHE[cache_key] = OpenAIChatModel(serving_name, provider=provider)
    return _MODEL_CACHE[cache_key]


def _extract_tokens(result: Any) -> tuple[int | None, int | None]:
    """Extract input_tokens and output_tokens from agent result usage."""
    if not hasattr(result, "usage"):
        return None, None
    usage_fn = getattr(result, "usage")
    usage = usage_fn() if callable(usage_fn) else usage_fn
    if usage is None:
        return None, None

    def _find_int(obj: Any, names: list[str]) -> int | None:
        for name in names:
            if hasattr(obj, name):
                val = getattr(obj, name)
                if val is not None:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        pass
            elif isinstance(obj, dict) and name in obj:
                val = obj[name]
                if val is not None:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        pass
        return None

    in_tokens = _find_int(usage, ["request_tokens", "input_tokens", "prompt_tokens"])
    out_tokens = _find_int(usage, ["response_tokens", "output_tokens", "completion_tokens"])
    return in_tokens, out_tokens


def _count_retries(result: Any) -> int:
    """Count retry prompts in the agent result messages."""
    messages: list[Any] = []
    if hasattr(result, "all_messages"):
        fn = getattr(result, "all_messages")
        messages = fn() if callable(fn) else fn
    elif hasattr(result, "messages"):
        fn = getattr(result, "messages")
        messages = fn() if callable(fn) else fn

    if not isinstance(messages, (list, tuple)):
        return 0

    retries = 0
    for msg in messages:
        kind = (
            getattr(msg, "kind", None)
            or getattr(msg, "role", None)
            or getattr(msg, "part_kind", None)
        )
        if kind == "retry-prompt" or "retryprompt" in type(msg).__name__.lower():
            retries += 1
            continue

        parts = getattr(msg, "parts", None)
        if parts and isinstance(parts, (list, tuple)):
            for part in parts:
                p_kind = getattr(part, "part_kind", None)
                if p_kind == "retry-prompt" or "retryprompt" in type(part).__name__.lower():
                    retries += 1
    return retries


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
    **run_kwargs: Any,
) -> Any:
    """Run agent.run_sync(user_prompt) and write an llm_runs record.

    Writes exactly one row to llm_runs for the call.
    On exception, writes one row with ok=0, null tokens and null retries, then re-raises.
    A failure in recording the run must not hide the original exception.
    """
    start_time = time.perf_counter()
    run_id = uuid.uuid4().hex
    try:
        result = agent.run_sync(user_prompt, **run_kwargs)
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        in_tokens, out_tokens = _extract_tokens(result)
        retries = _count_retries(result)
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
        except Exception:
            pass
        raise
