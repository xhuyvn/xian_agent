"""Unit tests for xn.config configuration loader."""

from __future__ import annotations

import builtins
from pathlib import Path
import sys
import unittest.mock
import pytest
import yaml

from xn.config import load_config


def test_config_precedence_and_nested_merging(tmp_path: Path) -> None:
    """Prove precedence novel > genre > global and recursive nested merging."""
    configs_dir = tmp_path / "configs"
    genres_dir = configs_dir / "genres"
    novels_dir = configs_dir / "novels"
    genres_dir.mkdir(parents=True)
    novels_dir.mkdir(parents=True)

    # 1. Global config
    global_file = configs_dir / "config.yaml"
    global_file.write_text(
        """
fallback_model: null
global_only_scalar: "from_global"
shared_scalar: "from_global"
genre_novel_scalar: "from_global"
genre_override_scalar: "from_global"
shared_list: ["global_item_1", "global_item_2"]
nested:
  global_key: "g_val"
  override_by_genre: "g_val"
  override_by_novel: "g_val"
  deep:
    level: "global"
    keep_me: true
""",
        encoding="utf-8",
    )

    # 2. Genre config
    genre_file = genres_dir / "fantasy.yaml"
    genre_file.write_text(
        """
genre_only_scalar: "from_genre"
shared_scalar: "from_genre"
genre_novel_scalar: "from_genre"
genre_override_scalar: "from_genre"
shared_list: ["genre_item"]
nested:
  genre_key: "genre_val"
  override_by_genre: "genre_val"
  override_by_novel: "genre_val"
  deep:
    level: "genre"
""",
        encoding="utf-8",
    )

    # 3. Novel config
    novel_file = novels_dir / "test_novel.yaml"
    novel_file.write_text(
        """
novel_id: test_novel
genre: fantasy
novel_only_scalar: "from_novel"
shared_scalar: "from_novel"
genre_novel_scalar: "from_novel"
shared_list: ["novel_item_1", "novel_item_2"]
nested:
  novel_key: "novel_val"
  override_by_novel: "novel_val"
  deep:
    level: "novel"
""",
        encoding="utf-8",
    )

    cfg = load_config("test_novel", configs_dir=configs_dir)

    # Precedence: novel > genre > global for scalars
    assert cfg["shared_scalar"] == "from_novel"
    assert cfg["genre_novel_scalar"] == "from_novel"
    assert cfg["genre_override_scalar"] == "from_genre"
    assert cfg["global_only_scalar"] == "from_global"
    assert cfg["genre_only_scalar"] == "from_genre"
    assert cfg["novel_only_scalar"] == "from_novel"

    # Lists are replaced by higher precedence tier, not concatenated or merged
    assert cfg["shared_list"] == ["novel_item_1", "novel_item_2"]

    # Nested dicts merge across all tiers
    assert cfg["nested"]["global_key"] == "g_val"
    assert cfg["nested"]["genre_key"] == "genre_val"
    assert cfg["nested"]["novel_key"] == "novel_val"
    assert cfg["nested"]["override_by_genre"] == "genre_val"
    assert cfg["nested"]["override_by_novel"] == "novel_val"

    # Deep nested dicts merge
    assert cfg["nested"]["deep"]["keep_me"] is True
    assert cfg["nested"]["deep"]["level"] == "novel"


def test_real_pntt1_config_loads() -> None:
    """Verify loading real pntt1 novel config located relative to this test file."""
    # configs/ is located relative to the test file: tests/unit/test_config.py -> ../../configs
    repo_root = Path(__file__).resolve().parents[2]
    configs_dir = repo_root / "configs"

    assert configs_dir.is_dir(), f"Expected configs directory at {configs_dir}"

    cfg = load_config("pntt1", configs_dir=configs_dir)

    # Acceptance criteria from brief
    assert cfg["genre"] == "xianxia"
    assert cfg["total_chapters"] == 2673
    assert "models" in cfg
    assert isinstance(cfg["models"], dict)
    assert "local_extractor" in cfg["models"]
    assert cfg["models"]["local_extractor"]["name"] == "deepseek-r1:14b"
    assert cfg["models"]["local_extractor"]["serving_name"] == "deepseek-r1-14b-12k"
    assert cfg["models"]["local_extractor"]["output_mode"] == "native"
    assert "arc_detection" in cfg
    assert cfg["arc_detection"]["max_arc_length"] == 90


def test_missing_novel_raises_file_not_found(tmp_path: Path) -> None:
    """Verify that a missing novel file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_novel", configs_dir=tmp_path)


def test_missing_genre_raises_file_not_found(tmp_path: Path) -> None:
    """Verify that a missing genre file referenced by a novel raises FileNotFoundError."""
    configs_dir = tmp_path / "configs"
    (configs_dir / "novels").mkdir(parents=True)
    (configs_dir / "genres").mkdir(parents=True)
    (configs_dir / "novels" / "novel_with_bad_genre.yaml").write_text(
        "novel_id: novel_with_bad_genre\ngenre: nonexistent_genre\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        load_config("novel_with_bad_genre", configs_dir=configs_dir)


def test_import_xn_config_does_not_read_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that importing xn.config performs no file I/O and no settings calls."""
    # Ensure necessary third-party modules are already loaded
    import yaml
    import pathlib

    # Force re-import of xn.config
    sys.modules.pop("xn.config", None)

    opened_non_code_files: list[str] = []
    orig_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        fname = str(file)
        # Allow Python interpreter to read source (.py) or bytecode (.pyc) during import
        if not (fname.endswith(".py") or fname.endswith(".pyc")):
            opened_non_code_files.append(fname)
        return orig_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    # Also assert yaml.safe_load is never called on module import
    mock_yaml_load = unittest.mock.MagicMock()
    monkeypatch.setattr(yaml, "safe_load", mock_yaml_load)

    import xn.config  # noqa: F401

    assert opened_non_code_files == [], f"Files read during import: {opened_non_code_files}"
    mock_yaml_load.assert_not_called()
