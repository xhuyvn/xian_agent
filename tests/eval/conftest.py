"""Options for the gold evaluation, which needs a real ingest and runs on demand only."""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--novel", default="pntt1", help="Novel ID to evaluate")
    parser.addoption("--gold-db", default="data/xn.db", help="Database holding the ingest")
    parser.addoption("--gold-report", default=None, help="Report path (default: data/eval/<novel>.json)")
    parser.addoption("--gold-configs-dir", default=None, help="Configs directory (default: the repository's configs)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip gold tests unless -m gold selects them, so a normal test run never needs a model."""
    if "gold" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="gold evaluation runs with -m gold")
    for item in items:
        if Path(str(item.path)).parent == Path(__file__).parent:
            item.add_marker(skip)
