"""LLM factory and execution logging layer for XN-Agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import logging
import time
from typing import Any, Callable, Literal
import uuid

from openai import AsyncOpenAI
from pydantic_ai import NativeOutput, capture_run_messages
from pydantic_ai.exceptions import ModelConnectionError, ModelHTTPError, ModelTimeoutError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from xn.config import CloudSettings
from xn.text import estimate_tokens

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[tuple[str, str], OpenAIChatModel] = {}

CLOUD_TIMEOUT_S = 90.0
CLOUD_RETRIES = 3
CLOUD_BACKOFF_S = 1.0
# A connection refused or reset within this time is retried after CLOUD_FAST_RETRY_S,
# since waiting longer does not help a dropped TLS handshake.
CLOUD_FAST_FAIL_S = 1.0
CLOUD_FAST_RETRY_S = 0.25
CLOUD_MAX_TOKENS = 2500

MAX_EVIDENCE_TOKENS = 20_000
MAX_CHUNKS = 8
MAX_ITEM_TOKENS = 450


class EgressError(ValueError):
    """A payload broke the egress rule and was not sent."""


class SpendCapError(RuntimeError):
    """The daily spend cap is unset, reached or cannot be checked."""


class UpstreamError(RuntimeError):
    """OpenRouter kept failing after the retries and the fallback model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CompactItem:
    """One piece of compact evidence, with the chapter it came from."""

    kind: Literal["event", "micro_summary", "digest", "chunk"]
    chapter_id: int
    text: str


def validate_compact(items: list[CompactItem], chapter_max: int) -> None:
    """Raise EgressError unless items fit the compact class and the spoiler bound."""
    allowed = {"event", "micro_summary", "digest", "chunk"}
    chunks = 0
    total = 0
    for item in items:
        if item.kind not in allowed:
            raise EgressError(f"Item kind not allowed in compact egress: {item.kind!r}")
        if not 1 <= item.chapter_id <= chapter_max:
            raise EgressError(f"Item from chapter {item.chapter_id} is outside 1..{chapter_max}")
        tokens = estimate_tokens(item.text)
        if tokens > MAX_ITEM_TOKENS:
            raise EgressError(f"Item has {tokens} tokens, over the {MAX_ITEM_TOKENS} limit")
        chunks += item.kind == "chunk"
        total += tokens
    if chunks > MAX_CHUNKS:
        raise EgressError(f"{chunks} chunks is over the limit of {MAX_CHUNKS}")
    if total > MAX_EVIDENCE_TOKENS:
        raise EgressError(f"Evidence has {total} tokens, over the {MAX_EVIDENCE_TOKENS} budget")


def _kind_attr(item: CompactItem) -> str:
    """Mark every kind but event, so event items render as they did before chunks existed."""
    return "" if item.kind == "event" else f' kind="{item.kind}"'


def render_compact_prompt(question: str, items: list[CompactItem]) -> str:
    """Build the user prompt, the only text that reaches OpenRouter besides the system prompt."""
    lines = [
        f'<item chapter="{i.chapter_id}"{_kind_attr(i)}>{escape(i.text, quote=False)}</item>'
        for i in items
    ]
    evidence = "\n".join(lines)
    return (
        f"<question>{escape(question, quote=False)}</question>\n"
        f"<evidence>\n{evidence}\n</evidence>"
    )


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


def cloud_model(model_name: str) -> OpenRouterModel:
    """Build an OpenRouter model on first use; retries live in run_compact, not the client."""
    cloud = CloudSettings()
    api_key = cloud.openrouter_api_key.get_secret_value()
    if not api_key:
        raise UpstreamError("UPSTREAM_AUTH", "OPENROUTER_API_KEY is not set")
    client = AsyncOpenAI(
        base_url=cloud.openrouter_base_url,
        api_key=api_key,
        timeout=CLOUD_TIMEOUT_S,
        max_retries=0,
    )
    return OpenRouterModel(model_name, provider=OpenRouterProvider(openai_client=client))


def extractor_output(output_type: Any, config: dict[str, Any]) -> Any:
    """Wrap output_type for models.local_extractor.output_mode (tool by default).

    Models that ignore tools, such as deepseek-r1, need "native", the JSON-schema
    response format.

    Raises:
        ValueError: If output_mode is neither "tool" nor "native".
    """
    mode = config.get("models", {}).get("local_extractor", {}).get("output_mode", "tool")
    if mode == "tool":
        return output_type
    if mode == "native":
        return NativeOutput(output_type)
    raise ValueError(f"Unknown models.local_extractor.output_mode: {mode!r}")


def extractor_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Build model settings from models.local_extractor temperature and think.

    think: false asks Ollama to skip thinking. When unset, the model's default applies.
    """
    models_cfg = config.get("models", {}).get("local_extractor", {})
    settings: dict[str, Any] = {"temperature": models_cfg.get("temperature", 0.1)}
    if models_cfg.get("think") is False:
        settings["openai_reasoning_effort"] = "none"
    return settings


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


def _check_spend_cap(repo: Any, config: dict[str, Any]) -> None:
    """Raise SpendCapError unless today's UTC cloud spend is known and under the cap."""
    cap = config.get("daily_spend_cap_usd")
    if cap is None:
        raise SpendCapError("daily_spend_cap_usd is not set")
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    spent, unknown = repo.cloud_spend_since(since=day_start.isoformat())
    if unknown:
        raise SpendCapError(f"{unknown} cloud calls today have no reported cost")
    if spent >= cap:
        raise SpendCapError(f"Daily spend ${spent:.4f} has reached the ${cap:.2f} cap")


def _reported_cost(messages: list[Any]) -> float | None:
    """Sum the cost OpenRouter reported on each response; None when a response has none."""
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    if not responses:
        return 0.0
    costs = [(r.provider_details or {}).get("cost") for r in responses]
    if any(cost is None for cost in costs):
        return None
    return float(sum(costs))


def _is_retryable(err: Exception) -> bool:
    """Tell whether err is a 429, a 5xx, a timeout or a lost connection."""
    if isinstance(err, ModelHTTPError):
        return err.status_code == 429 or err.status_code >= 500
    return isinstance(err, ModelConnectionError)


def _is_fast_drop(err: Exception, seconds: float) -> bool:
    """Tell whether err is a connection that dropped at once, not a timeout or an HTTP error."""
    return (
        isinstance(err, ModelConnectionError)
        and not isinstance(err, ModelTimeoutError)
        and seconds < CLOUD_FAST_FAIL_S
    )


def _upstream_code(err: Exception) -> str:
    """Map a final OpenRouter failure to its API error code."""
    if isinstance(err, ModelHTTPError) and err.status_code == 429:
        return "UPSTREAM_RATE_LIMIT"
    if isinstance(err, ModelTimeoutError):
        return "UPSTREAM_TIMEOUT"
    return "UPSTREAM_ERROR"


def run_compact(
    agent: Any,
    question: str,
    items: list[CompactItem],
    *,
    repo: Any,
    novel_id: str,
    stage: str,
    chapter_max: int,
    model_name: str,
    prompt_version: str,
    config: dict[str, Any],
    temperature: float,
    egress_class: str = "compact",
    model_factory: Callable[[str], Any] = cloud_model,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Send compact evidence to OpenRouter and write one llm_runs row per attempt.

    The prompt is built here from items, so nothing but a checked payload leaves the machine.
    Retries 429/5xx and connection failures with exponential backoff, then tries
    fallback_model once if it is configured. A connection that drops at once is retried
    after a short pause instead. cloud_provider in config, if set, is OpenRouter's
    provider routing (for example {sort: latency}).

    Raises:
        EgressError: If the class is not compact or items break the compact rule.
        SpendCapError: If the daily cap is unset, reached or cannot be checked.
        UpstreamError: If the upstream failures outlast the retries and the fallback.
    """
    if egress_class != "compact":
        raise EgressError(f"Egress class not allowed here: {egress_class!r}")
    validate_compact(items, chapter_max)
    user_prompt = render_compact_prompt(question, items)
    payload_chapters = ",".join(str(c) for c in sorted({i.chapter_id for i in items}))
    settings = {
        "temperature": temperature,
        "max_tokens": CLOUD_MAX_TOKENS,
        "openrouter_usage": {"include": True},
    }
    if config.get("cloud_provider"):
        settings["openrouter_provider"] = dict(config["cloud_provider"])

    attempts = [(model_name, retry) for retry in range(CLOUD_RETRIES + 1)]
    fallback = config.get("fallback_model")
    if fallback:
        attempts.append((fallback, 0))

    last_err: Exception | None = None
    fast_retry = False
    for name, retry in attempts:
        if name == model_name and retry > 0:
            sleep(CLOUD_FAST_RETRY_S if fast_retry else CLOUD_BACKOFF_S * 2 ** (retry - 1))
        _check_spend_cap(repo, config)

        start_time = time.perf_counter()
        run_id = uuid.uuid4().hex
        result = None
        error: Exception | None = None
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(
                    user_prompt, model=model_factory(name), model_settings=settings
                )
            except Exception as err:
                error = err

        seconds = time.perf_counter() - start_time
        usage = result.usage if result is not None else None
        cost = _reported_cost(result.all_messages() if result is not None else messages)
        repo.add_llm_run(
            novel_id=novel_id,
            run_id=run_id,
            stage=stage,
            chapter_id=None,
            model=name,
            prompt_version=prompt_version,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            latency_ms=int(seconds * 1000),
            retries=retry,
            ok=1 if result is not None else 0,
            cost_usd=cost,
            egress_class=egress_class,
            payload_chapters=payload_chapters,
        )
        if result is not None:
            return result
        if not _is_retryable(error):
            raise error
        last_err = error
        fast_retry = _is_fast_drop(error, seconds)
        logger.warning("OpenRouter call to %s failed on try %d: %s", name, retry + 1, error)

    raise UpstreamError(_upstream_code(last_err), str(last_err)) from last_err
