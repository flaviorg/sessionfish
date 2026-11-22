from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sessionfish import scratch

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DEMO_JSONL = EXAMPLES / "sessions" / "weakened-assert.jsonl"
DEMO_SCOPE = tuple(json.loads((EXAMPLES / "sessions" / "weakened-assert.json").read_text())["scope"])


def make_fixture_repo(dest: Path) -> tuple[Path, str]:
    repo = dest / "repo"
    shutil.copytree(EXAMPLES / "discount_shop", repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return repo, scratch.init_fixture_repo(repo)


@pytest.fixture
def fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    return make_fixture_repo(tmp_path)


@pytest.fixture(scope="session")
def demo_report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One full offline review of the demo session, shared by the slow tests."""
    from sessionfish.evaluate import EvalSettings
    from sessionfish.llm.demo_script import demo_llm
    from sessionfish.review import Baseline, ReviewOptions, review

    base = tmp_path_factory.mktemp("demo")
    repo, sha = make_fixture_repo(base)
    events: list[tuple[str, dict]] = []
    report = review(
        DEMO_JSONL,
        repo,
        Baseline(sha),
        base / "out",
        demo_llm(),
        ReviewOptions(
            settings=EvalSettings(scope=DEMO_SCOPE),
            answer=lambda payload: {"accept": True},
            on_event=lambda kind, data: events.append((kind, data)),
        ),
    )
    report["_events"] = events
    report["_repo"] = str(repo)
    return report
