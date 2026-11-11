r"""Model pools: free-quota cloud models used in order, with failover when a quota runs out (G-32).

A pool is a list of models on one OpenAI-compatible endpoint (for example Alibaba Model Studio).
Each call goes to the first model whose quota is not used up. When the endpoint answers that a
model's quota is used up, the model is marked exhausted in the database (table model_quota), so
later calls and resumed runs skip it, and the same call moves on to the next model. When every
model is exhausted, PoolExhaustedError stops the stage.

Config (all optional; without stage_pools every stage keeps its usual model):

    model_pools:
      qwen_free:
        provider: dashscope            # key for the exhausted-model records (default: pool name)
        base_url: https://.../compatible-mode/v1
        api_key_setting: dashscope_api_key
        extra_body: {enable_thinking: false}
        models: [qwen3.8-max, {name: qwen3.8-27b, extra_body: {enable_thinking: true}}]
        exhausted_patterns: [...]      # optional: error text that means "quota used up"
    stage_pools: {s5: qwen_free, prefix: qwen_free, s7: qwen_free, s6: deepseek_free}
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider

from xn.config import CloudSettings
from xn.llm import local_model

logger = logging.getLogger(__name__)

# Error text that means a model's (free) quota is used up, not a rate limit or a passing failure.
DEFAULT_EXHAUSTED_PATTERNS = (
    "FreeTierOnly",
    "free tier",
    "Arrearage",
    "insufficient_quota",
    "AccessDenied.Unpurchased",
    "quota has been exhausted",
    "exceeded your current quota",
)
# Settings a pool member passes on; provider-specific ones (OpenRouter, Ollama reasoning) are dropped.
KEPT_SETTINGS = ("temperature", "max_tokens", "top_p", "seed", "timeout")


class PoolExhaustedError(RuntimeError):
    r"""Every model of a pool has used up its quota."""


class MemberExhausted(Exception):
    r"""One pool member is used up; the pool moves on to the next."""


@dataclass
class PoolState:
    r"""Exhausted members and the last member that answered, shared by a pool's members."""

    name: str
    provider: str
    patterns: tuple[str, ...]
    repo: Any = None
    novel_id: str | None = None
    exhausted: set[str] = field(default_factory=set)
    last_used: str | None = None

    def is_quota_error(self, err: Exception) -> bool:
        text = f"{getattr(err, 'body', '')} {err}".lower()
        return isinstance(err, ModelHTTPError) and any(p.lower() in text for p in self.patterns)

    def mark(self, model: str, err: Exception) -> None:
        self.exhausted.add(model)
        logger.warning("Pool %s: %s quota used up, moving to the next model: %s", self.name, model, err)
        if self.repo is not None and self.novel_id is not None:
            self.repo.mark_model_exhausted(
                novel_id=self.novel_id, provider=self.provider, model=model, error=str(err)[:500]
            )


class PoolMember(WrapperModel):
    r"""One model of a pool: skipped when exhausted, marked exhausted on a quota error."""

    def __init__(self, wrapped: Model, name: str, state: PoolState, extra_body: dict[str, Any] | None):
        super().__init__(wrapped)
        self.member_name = name
        self._pool_state = state
        self._pool_extra_body = dict(extra_body or {})

    def _member_settings(self, model_settings: Any) -> dict[str, Any]:
        kept = {k: v for k, v in dict(model_settings or {}).items() if k in KEPT_SETTINGS}
        if self._pool_extra_body:
            kept["extra_body"] = dict(self._pool_extra_body)
        return kept

    async def request(self, messages: list[Any], model_settings: Any, model_request_parameters: ModelRequestParameters) -> Any:
        if self.member_name in self._pool_state.exhausted:
            raise MemberExhausted(self.member_name)
        try:
            response = await self.wrapped.request(messages, self._member_settings(model_settings), model_request_parameters)
        except ModelHTTPError as err:
            if self._pool_state.is_quota_error(err):
                self._pool_state.mark(self.member_name, err)
                raise MemberExhausted(self.member_name) from err
            raise
        self._pool_state.last_used = self.member_name
        return response


class _PoolEnd(WrapperModel):
    r"""Last link of the chain: reached only when every member is exhausted."""

    def __init__(self, wrapped: Model, state: PoolState):
        super().__init__(wrapped)
        self._pool_state = state

    async def request(self, messages: list[Any], model_settings: Any, model_request_parameters: ModelRequestParameters) -> Any:
        raise PoolExhaustedError(
            f"model pool {self._pool_state.name}: every model's quota is used up ({', '.join(sorted(self._pool_state.exhausted))})"
        )


class PoolModel(FallbackModel):
    r"""A FallbackModel over pool members that only falls back when a member is used up."""

    def __init__(self, members: list[PoolMember], state: PoolState):
        super().__init__(*members, _PoolEnd(members[-1].wrapped, state), fallback_on=(MemberExhausted,))
        self.state = state

    @property
    def last_used(self) -> str | None:
        return self.state.last_used


def _member_model(name: str, base_url: str, api_key: str) -> OpenAIChatModel:
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return OpenAIChatModel(name, provider=OpenAIProvider(openai_client=client))


def build_pool(config: dict[str, Any], pool_name: str, *, repo: Any = None, novel_id: str | None = None) -> PoolModel:
    r"""Build the pool named pool_name from config["model_pools"]; already exhausted models are skipped."""
    spec = (config.get("model_pools") or {}).get(pool_name)
    if not spec or not spec.get("models"):
        raise ValueError(f"model_pools.{pool_name} is missing or has no models")
    key_setting = spec.get("api_key_setting")
    api_key = "none"
    if key_setting:
        secret = getattr(CloudSettings(), str(key_setting), None)
        api_key = secret.get_secret_value() if secret is not None else ""
        if not api_key:
            raise ValueError(f"model_pools.{pool_name}.api_key_setting: {key_setting} is not set")
    state = PoolState(
        name=pool_name,
        provider=str(spec.get("provider") or pool_name),
        patterns=tuple(spec.get("exhausted_patterns") or DEFAULT_EXHAUSTED_PATTERNS),
        repo=repo,
        novel_id=novel_id,
    )
    if repo is not None and novel_id is not None:
        state.exhausted |= repo.exhausted_models(novel_id=novel_id, provider=state.provider)
    members = []
    for entry in spec["models"]:
        # An entry is a model name, or {name, extra_body} for a model that needs its own settings
        # (for example one that only accepts enable_thinking: true).
        name = str(entry["name"] if isinstance(entry, dict) else entry)
        extra = {**(spec.get("extra_body") or {}), **((entry.get("extra_body") or {}) if isinstance(entry, dict) else {})}
        members.append(PoolMember(_member_model(name, spec["base_url"], api_key), name, state, extra))
    return PoolModel(members, state)


def stage_pool_name(config: dict[str, Any], stage: str) -> str | None:
    r"""The pool configured for a stage in stage_pools, else None."""
    name = (config.get("stage_pools") or {}).get(stage)
    return str(name) if name else None


def stage_model(config: dict[str, Any], stage: str, *, repo: Any = None, novel_id: str | None = None) -> Model:
    r"""The model a local-model stage runs on: its pool when stage_pools names one, else local_extractor."""
    pool = stage_pool_name(config, stage)
    if pool is None:
        return local_model(config)
    return build_pool(config, pool, repo=repo, novel_id=novel_id)


def answered_by(model: Any, default: str) -> str:
    r"""The model name to record for a call: the pool member that answered, else default."""
    used = getattr(model, "last_used", None)
    return str(used) if used else default
