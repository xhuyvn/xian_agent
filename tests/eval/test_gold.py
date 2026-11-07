"""Gold evaluation: pytest tests/eval -m gold --novel pntt1 [--gold-db <path>]."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any

import pytest

from xn.config import load_config
from xn.gold import MIN_RECALL, GoldSet, gold_report, load_gold, validate_gold
from xn.storage.database import connect
from xn.storage.repository import Repository

pytestmark = pytest.mark.gold

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def config(request: pytest.FixtureRequest) -> dict[str, Any]:
    configs_dir = request.config.getoption("--gold-configs-dir")
    return load_config(
        request.config.getoption("--novel"), configs_dir=Path(configs_dir) if configs_dir else REPO_ROOT / "configs"
    )


@pytest.fixture(scope="module")
def gold(config: dict[str, Any]) -> GoldSet:
    return load_gold(REPO_ROOT / "eval" / config["novel_id"])


@pytest.fixture(scope="module")
def repo(request: pytest.FixtureRequest) -> Iterator[Repository]:
    path = Path(request.config.getoption("--gold-db"))
    if not path.exists():
        pytest.fail(f"No ingest at {path}; run xn run first or pass --gold-db")
    conn = connect(path)
    yield Repository(conn)
    conn.close()


@pytest.fixture(scope="module")
def report(
    request: pytest.FixtureRequest, repo: Repository, gold: GoldSet, config: dict[str, Any]
) -> dict[str, Any]:
    result = gold_report(repo, gold=gold, config=config, prompts_dir=REPO_ROOT / "prompts")
    target = request.config.getoption("--gold-report")
    path = Path(target) if target else Path("data/eval") / f"{config['novel_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def test_gold_assets_match_the_text(repo: Repository, gold: GoldSet, config: dict[str, Any]) -> None:
    rows = repo.list_chapters(novel_id=config["novel_id"], chapter_max=gold.meta["chapter_max"])
    problems = validate_gold(
        gold,
        {row["chapter_id"]: row["clean_text"] for row in rows},
        categories=config["categories"],
        event_types=config["event_types"],
    )
    assert not problems, "\n".join(problems)


def test_extraction_recall(report: dict[str, Any]) -> None:
    assert report["extraction"]["recall"] >= MIN_RECALL


def test_evidence_stays_within_the_bound(report: dict[str, Any]) -> None:
    assert not [d for d in report["spoilers"]["details"] if "above bound" in d]
