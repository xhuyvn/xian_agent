"""Dependencies shared by query graph nodes; kept out of the checkpointed state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable

from xn.llm import cloud_model
from xn.storage.repository import Repository


class QueryError(RuntimeError):
    """A query that cannot run, with its API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GraphDeps:
    """What nodes need besides state; router_model overrides the local model in tests."""

    repo: Repository
    config: dict[str, Any]
    prompts_dir: Path = Path("prompts")
    router_model: Any = None
    cloud_factory: Callable[[str], Any] = cloud_model
    sleep: Callable[[float], None] = time.sleep


def prompt_version(deps: GraphDeps, name: str) -> str:
    """Return the prompt version pinned in config, v1 by default."""
    return (deps.config.get("prompts") or {}).get(name, "v1")
