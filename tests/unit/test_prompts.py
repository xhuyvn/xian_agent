"""Unit tests for prompt loading and template rendering."""

from __future__ import annotations

import builtins
from pathlib import Path
import sys
import jinja2
import pytest

from xn.config import load_config
from xn.prompts import load_prompt, profile_from_config


def test_real_v1_prompt_renders_with_xianxia_profile() -> None:
    """Verify loading real v1 prompt with xianxia profile and empty realm ladder."""
    repo_root = Path(__file__).resolve().parents[2]
    config = load_config("pntt1", configs_dir=repo_root / "configs")
    profile = profile_from_config(config)

    # In pntt1.yaml, realm_ladder is empty list
    assert profile["realm_ladder"] == []
    assert len(profile["categories"]) > 0
    assert len(profile["event_types"]) > 0
    assert len(profile["extraction_hints"]) > 0

    loaded = load_prompt("s1_extract", "v1", profile, prompts_dir=repo_root / "prompts")

    # Prompt version is 8 hex characters
    assert len(loaded.prompt_version) == 8
    assert all(c in "0123456789abcdef" for c in loaded.prompt_version)

    # Categories and event types are rendered
    for cat in profile["categories"]:
        assert cat in loaded.text
    for etype in profile["event_types"]:
        assert etype in loaded.text

    # Extraction hints are rendered
    for hint in profile["extraction_hints"]:
        assert hint in loaded.text

    # Security requirement: <chapter> tags declared as data, not instructions
    assert "<chapter>" in loaded.text
    assert "DỮ LIỆU" in loaded.text

    # Evidence quote requirement: verbatim quote of at most 25 words
    assert "25" in loaded.text
    assert "evidence" in loaded.text


def test_real_v1_prompt_renders_with_populated_realm_ladder() -> None:
    """Verify v1 prompt renders realm ladder section when provided."""
    repo_root = Path(__file__).resolve().parents[2]
    config = load_config("pntt1", configs_dir=repo_root / "configs")
    profile = profile_from_config(config)
    profile["realm_ladder"] = ["Luyện Khí", "Trúc Cơ", "Kết Đan", "Nguyên Anh"]

    loaded = load_prompt("s1_extract", "v1", profile, prompts_dir=repo_root / "prompts")

    for realm in profile["realm_ladder"]:
        assert realm in loaded.text
    assert "Thang cảnh giới" in loaded.text


def test_front_matter_is_absent_from_rendered_text() -> None:
    """Verify front matter metadata and delimiters are removed from rendered text."""
    repo_root = Path(__file__).resolve().parents[2]
    config = load_config("pntt1", configs_dir=repo_root / "configs")
    profile = profile_from_config(config)

    loaded = load_prompt("s1_extract", "v1", profile, prompts_dir=repo_root / "prompts")

    assert "---" not in loaded.text
    assert "name: s1_extract" not in loaded.text
    assert "model_family: qwen" not in loaded.text
    assert "output_schema: ChapterExtraction" not in loaded.text
    assert "changelog:" not in loaded.text


def test_prompt_version_identical_for_lf_and_crlf(tmp_path: Path) -> None:
    """Verify prompt_version is 8 hex characters and identical for LF and CRLF copies."""
    template_content = (
        "---\n"
        "name: test_prompt\n"
        "version: v1\n"
        "model_family: qwen\n"
        "output_schema: ChapterExtraction\n"
        "genre_profiles: [xianxia]\n"
        "changelog: test\n"
        "---\n"
        "Prompt body for {{ categories[0] }}.\n"
    )

    # 1. LF file
    lf_dir = tmp_path / "lf" / "test_prompt"
    lf_dir.mkdir(parents=True)
    lf_file = lf_dir / "v1.md"
    lf_file.write_bytes(template_content.encode("utf-8"))

    # 2. CRLF file
    crlf_dir = tmp_path / "crlf" / "test_prompt"
    crlf_dir.mkdir(parents=True)
    crlf_file = crlf_dir / "v1.md"
    crlf_bytes = template_content.replace("\n", "\r\n").encode("utf-8")
    crlf_file.write_bytes(crlf_bytes)

    dummy_profile = {
        "categories": ["character"],
        "event_types": {},
        "extraction_hints": [],
        "realm_ladder": [],
    }

    lf_loaded = load_prompt("test_prompt", "v1", dummy_profile, prompts_dir=tmp_path / "lf")
    crlf_loaded = load_prompt("test_prompt", "v1", dummy_profile, prompts_dir=tmp_path / "crlf")

    assert len(lf_loaded.prompt_version) == 8
    assert all(c in "0123456789abcdef" for c in lf_loaded.prompt_version)
    assert lf_loaded.prompt_version == crlf_loaded.prompt_version
    assert lf_loaded.text == crlf_loaded.text


def test_missing_placeholder_raises_undefined_error(tmp_path: Path) -> None:
    """Verify Jinja2 StrictUndefined causes missing template placeholders to raise."""
    prompts_dir = tmp_path / "prompts"
    prompt_dir = prompts_dir / "strict_test"
    prompt_dir.mkdir(parents=True)

    template_file = prompt_dir / "v1.md"
    template_file.write_text(
        "---\nname: strict\nversion: v1\n---\nHello {{ missing_variable }}!\n",
        encoding="utf-8",
    )

    with pytest.raises(jinja2.exceptions.UndefinedError):
        load_prompt("strict_test", "v1", {}, prompts_dir=prompts_dir)


def test_import_xn_prompts_does_not_read_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify importing xn.prompts performs no file I/O."""
    sys.modules.pop("xn.prompts", None)

    opened_non_code_files: list[str] = []
    orig_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        fname = str(file)
        if not (fname.endswith(".py") or fname.endswith(".pyc")):
            opened_non_code_files.append(fname)
        return orig_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    import xn.prompts  # noqa: F401

    assert opened_non_code_files == []
