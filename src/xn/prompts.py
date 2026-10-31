"""Prompt loading and template rendering for XN-Agent."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any
from jinja2 import Environment, StrictUndefined


@dataclass(frozen=True)
class LoadedPrompt:
    """Loaded prompt with rendered text and content version hash."""

    text: str
    prompt_version: str


def _strip_front_matter(content: str) -> str:
    """Remove YAML front matter delimited by '---' from template text."""
    lines = content.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:]).lstrip("\n")
    return content


def profile_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """Build profile dict holding categories, event_types, extraction_hints, realm_ladder."""
    return {
        "categories": list(config.get("categories") or []),
        "event_types": dict(config.get("event_types") or {}),
        "extraction_hints": list(config.get("extraction_hints") or []),
        "realm_ladder": list(config.get("realm_ladder") or []),
    }


def load_prompt(
    name: str,
    version: str,
    profile: dict[str, Any],
    prompts_dir: Path = Path("prompts"),
) -> LoadedPrompt:
    """Load and render a prompt template with the provided genre profile.

    Args:
        name: Subdirectory name under prompts_dir (e.g. 's1_extract').
        version: Version file name without extension (e.g. 'v1').
        profile: Dictionary holding template variables.
        prompts_dir: Base directory for prompt templates.

    Returns:
        LoadedPrompt dataclass with rendered text and 8-character sha256 prompt_version.

    Raises:
        FileNotFoundError: If the prompt template file does not exist.
        jinja2.exceptions.UndefinedError: If any placeholder is missing from profile.
    """
    prompt_path = Path(prompts_dir) / name / f"{version}.md"
    if not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    raw_text = prompt_path.read_text(encoding="utf-8")
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    prompt_version = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]

    body = _strip_front_matter(normalized)
    env = Environment(undefined=StrictUndefined)
    template = env.from_string(body)
    rendered_text = template.render(**profile)

    return LoadedPrompt(text=rendered_text, prompt_version=prompt_version)
