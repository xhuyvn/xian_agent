"""Three-level configuration management for XN-Agent.

Loads and merges configuration across three tiers:
1. Global configuration (configs/config.yaml)
2. Genre profile (configs/genres/<genre>.yaml)
3. Novel configuration (configs/novels/<novel_id>.yaml)

Precedence: novel > genre > global.
Nested dicts merge; lists and scalars are replaced.
Importing this module performs no file I/O and instantiates no Settings.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict
import yaml


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively deep-merge two dictionaries.

    Nested dicts are merged recursively. Lists and scalars in override replace
    those in base.
    """
    merged = copy.deepcopy(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def load_config(novel_id: str, configs_dir: Path = Path("configs")) -> dict:
    """Load and merge global, genre, and novel configurations.

    Reads:
    1. config.yaml (global)
    2. genres/<genre>.yaml (genre profile, where genre comes from the novel config)
    3. novels/<novel_id>.yaml (novel-specific configuration)

    Deep-merges them with precedence novel > genre > global.
    Nested dicts merge; lists and scalars are replaced.

    Args:
        novel_id: Slug of the novel (e.g. 'pntt1').
        configs_dir: Path to directory containing configs (default: Path("configs")).

    Returns:
        Merged configuration dictionary.

    Raises:
        FileNotFoundError: If the novel file or referenced genre file does not exist.
    """
    configs_path = Path(configs_dir)

    # 1. Read novel configuration
    novel_file = configs_path / "novels" / f"{novel_id}.yaml"
    if not novel_file.is_file():
        raise FileNotFoundError(f"Novel configuration not found: {novel_file}")

    with open(novel_file, "r", encoding="utf-8") as f:
        novel_cfg = yaml.safe_load(f) or {}

    if not isinstance(novel_cfg, dict):
        novel_cfg = {}

    genre = novel_cfg.get("genre")
    if not genre:
        raise FileNotFoundError(f"Novel config '{novel_id}' missing 'genre' field in {novel_file}")

    # 2. Read genre configuration
    genre_file = configs_path / "genres" / f"{genre}.yaml"
    if not genre_file.is_file():
        raise FileNotFoundError(f"Genre configuration not found: {genre_file}")

    with open(genre_file, "r", encoding="utf-8") as f:
        genre_cfg = yaml.safe_load(f) or {}

    if not isinstance(genre_cfg, dict):
        genre_cfg = {}

    # 3. Read global configuration
    global_file = configs_path / "config.yaml"
    if global_file.is_file():
        with open(global_file, "r", encoding="utf-8") as f:
            global_cfg = yaml.safe_load(f) or {}
        if not isinstance(global_cfg, dict):
            global_cfg = {}
    else:
        global_cfg = {}

    # Deep-merge: global -> genre -> novel
    merged = _deep_merge(global_cfg, genre_cfg)
    merged = _deep_merge(merged, novel_cfg)

    return merged
