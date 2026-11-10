r"""Unit tests for models.<key>.api_key_setting and extra_body (G-31.1, cloud confirmer test)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xn import llm
from xn.llm import extractor_settings, local_model

SETTING_ENV = "DASHSCOPE" + "_API_KEY"  # the CloudSettings field dashscope_api_key


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(llm, "_MODEL_CACHE", llm.threading.local())
    monkeypatch.chdir(tmp_path)  # no settings file here, so only the environment counts
    monkeypatch.delenv(SETTING_ENV, raising=False)


def test_local_model_takes_its_key_from_the_named_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SETTING_ENV, "test-key-123")
    cfg = {"models": {"s2_confirmer": {"serving_name": "qwen3.8-flash", "base_url": "https://example.test/v1",
                                       "api_key_setting": "dashscope_api_key"}}}
    model = local_model(cfg, "s2_confirmer")
    assert model.client.api_key == "test-key-123"
    assert str(model.client.base_url).startswith("https://example.test/v1")


@pytest.mark.parametrize("setting", ["dashscope_api_key", "no_such_setting"])
def test_local_model_refuses_an_empty_or_unknown_key_setting(setting: str) -> None:
    cfg = {"models": {"x": {"serving_name": "m", "api_key_setting": setting}}}
    with pytest.raises(ValueError, match=f"models.x.api_key_setting: {setting} is not set"):
        local_model(cfg, "x")


def test_local_model_without_key_setting_keeps_the_ollama_key() -> None:
    assert local_model({"models": {"x": {"serving_name": "m"}}}, "x").client.api_key == "ollama"


def test_extractor_settings_pass_extra_body() -> None:
    cfg = {"models": {"x": {"temperature": 0.1, "extra_body": {"enable_thinking": False}}}}
    assert extractor_settings(cfg, "x") == {"temperature": 0.1, "extra_body": {"enable_thinking": False}}
    assert "extra_body" not in extractor_settings({"models": {"x": {}}}, "x")
