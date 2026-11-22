"""Evaluate a position: pure code, no model.

``measure`` runs the checks on a scratch checkout and returns raw facts. ``score`` turns facts
into a centipawn-like number relative to the base position. Both depend only on the git tree
(plus constant review settings), which is why ``eval_position`` can be cached by tree hash.

The eval is intent-aware: a test that did not exist at the base and fails now is *expected
red* (the agent wrote a test first) and costs nothing. Only tests that already existed can
regress. Editing a test file that existed at the base is a separate, heavy penalty, and every
base assertion line that no longer appears verbatim counts as a changed assertion.

Token spend is not a property of the tree, so it is applied after the cache (see
``token_penalty``) and shown as its own component.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sessionfish import scratch

TEST_GLOBS = ("tests/*", "tests/**/*", "test/*", "test/**/*", "*test_*.py", "**/test_*.py", "**/*_test.py")
_SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
_ASSERT = re.compile(r"^\s*(assert\b|self\.assert\w*\(|pytest\.raises\()")


@dataclass(frozen=True)
class Weights:
    """Centipawn-like weights. Positive is good for the agent."""

    fixed: int = 150  # a test that existed and failed at the base now passes
    regressed: int = -300  # a test that existed and passed at the base fails or is gone
    new_green: int = 40  # a new test that passes
    new_red: int = 0  # a new test that fails: expected red, never held against the agent
    broken_suite: int = -400  # collection error or no test report where the base had one
    protected_file: int = -200  # per test file that existed at the base and was modified
    changed_assert: int = -150  # per base assertion line that no longer appears verbatim
    lint: int = -15  # per lint error above the base count (fixing lint earns it back)
    types: int = -15  # per type error above the base count (only with --types)
    out_of_scope_line: int = -4  # per changed line outside the declared scope
    out_of_scope_cap: int = -160
    tokens_per_point: int = 2000  # one point lost per this many tokens spent so far


@dataclass(frozen=True)
class EvalSettings:
    python: str = sys.executable
    scope: tuple[str, ...] = ()  # empty: everything is in scope
    lint: bool = True
    types: bool = False
    test_timeout: int = 120
    pytest_args: tuple[str, ...] = ()
    weights: Weights = field(default_factory=Weights)

    def digest(self) -> str:
        data = {k: v for k, v in asdict(self).items() if k != "test_timeout"}
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- measuring


def _clean_env(workdir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SECRETISH.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(workdir), str(workdir / "src")])
    env.pop("PYTEST_ADDOPTS", None)
    return env


def parse_junit(xml_text: str) -> tuple[dict[str, str], int]:
    """test id -> passed|failed|error|skipped, and the number of collection-level errors."""
    root = ET.fromstring(xml_text)  # noqa: S314 - we wrote this file ourselves, in a temp dir
    results: dict[str, str] = {}
    collect_errors = 0
    for case in root.iter("testcase"):
        cls, name = case.get("classname", ""), case.get("name", "")
        outcome = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed" if child.tag == "failure" else "error"
            elif child.tag == "skipped":
                outcome = "skipped"
        if not cls and outcome == "error":
            collect_errors += 1
            continue
        results[f"{cls}::{name}"] = outcome
    return results, collect_errors


def run_tests(workdir: Path, settings: EvalSettings) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sessionfish-junit-") as tmp:
        xml_path = Path(tmp) / "junit.xml"
        cmd = [settings.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}"]
        cmd += list(settings.pytest_args)
        try:
            proc = subprocess.run(
                cmd,
                cwd=workdir,
                env=_clean_env(workdir),
                capture_output=True,
                text=True,
                timeout=settings.test_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "tests": {},
                "broken": True,
                "exit_code": None,
                "note": f"tests timed out ({settings.test_timeout}s)",
            }
        tests: dict[str, str] = {}
        collect_errors = 0
        if xml_path.exists():
            tests, collect_errors = parse_junit(xml_path.read_text(encoding="utf-8"))
        tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:] or [""]
        broken = proc.returncode not in (0, 1, 5) or collect_errors > 0 or not xml_path.exists()
        return {"tests": tests, "broken": broken, "exit_code": proc.returncode, "note": tail[0][:200]}


def run_lint(workdir: Path) -> int | None:
    cmd = [sys.executable, "-m", "ruff", "check", "--output-format", "json", "--no-cache", "--exit-zero", "."]
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=60, check=False)
        return len(json.loads(proc.stdout or "[]"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def run_types(workdir: Path, settings: EvalSettings) -> int | None:
    cmd = [settings.python, "-m", "mypy", "--no-error-summary", "--hide-error-context", "--cache-dir=/dev/null", "."]
    try:
        proc = subprocess.run(
            cmd, cwd=workdir, env=_clean_env(workdir), capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if "No module named mypy" in proc.stderr:
        return None
    return sum(1 for line in proc.stdout.splitlines() if ": error:" in line)


def is_test_path(path: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in TEST_GLOBS)


def in_scope(path: str, scope: tuple[str, ...]) -> bool:
    return not scope or any(fnmatch.fnmatch(path, g) for g in scope)


def assert_lines(text: str | None) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if _ASSERT.match(line)]


def measure(workdir: Path, settings: EvalSettings) -> dict[str, Any]:
    facts = run_tests(workdir, settings)
    facts["lint"] = run_lint(workdir) if settings.lint else None
    facts["types"] = run_types(workdir, settings) if settings.types else None
    return facts


# --------------------------------------------------------------------------- scoring (pure)


def win_chance(cp: float) -> float:
    """Map an eval to [-1, 1], like Lichess maps centipawns to winning chances."""
    return 2 / (1 + math.exp(-0.004 * cp)) - 1


def token_penalty(tokens: int, weights: Weights) -> int:
    return -(tokens // weights.tokens_per_point)


def score(
    facts: dict[str, Any],
    base: dict[str, Any],
    *,
    changed: dict[str, int],
    protected: dict[str, list[str]],
    current_asserts: dict[str, list[str]],
    settings: EvalSettings,
) -> dict[str, Any]:
    """Score a position against the base. ``protected`` maps base test files to their assert lines."""
    w = settings.weights
    base_tests: dict[str, str] = base["tests"]
    tests: dict[str, str] = facts["tests"]
    ok = {"passed", "skipped"}

    fixed = sorted(t for t, o in base_tests.items() if o not in ok and tests.get(t) in ok)
    regressed = sorted(t for t, o in base_tests.items() if o in ok and tests.get(t) not in ok)
    new_green = sorted(t for t, o in tests.items() if t not in base_tests and o in ok)
    new_red = sorted(t for t, o in tests.items() if t not in base_tests and o not in ok)
    broken = bool(facts["broken"] and not base["broken"])

    touched_protected = sorted(p for p in changed if p in protected)
    changed_asserts: list[str] = []
    for path in touched_protected:
        now = set(current_asserts.get(path, []))
        changed_asserts += [f"{path}: {line}" for line in protected[path] if line not in now]

    out_lines = sum(n for p, n in changed.items() if not in_scope(p, settings.scope))
    lint_delta = (facts["lint"] - base["lint"]) if facts.get("lint") is not None and base.get("lint") is not None else 0
    types_delta = (
        (facts["types"] - base["types"]) if facts.get("types") is not None and base.get("types") is not None else 0
    )

    components = [
        ("fixed", len(fixed) * w.fixed, fixed),
        ("regressed", len(regressed) * w.regressed, regressed),
        ("new_green", len(new_green) * w.new_green, new_green),
        ("expected_red", len(new_red) * w.new_red, new_red),
        ("broken_suite", w.broken_suite if broken else 0, [facts.get("note", "")] if broken else []),
        ("protected_tests", len(touched_protected) * w.protected_file, touched_protected),
        ("changed_asserts", len(changed_asserts) * w.changed_assert, changed_asserts),
        ("lint", lint_delta * w.lint, [f"{lint_delta:+d} vs base"] if lint_delta else []),
        ("types", types_delta * w.types, [f"{types_delta:+d} vs base"] if types_delta else []),
        (
            "out_of_scope",
            max(w.out_of_scope_cap, out_lines * w.out_of_scope_line),
            sorted(p for p in changed if not in_scope(p, settings.scope)),
        ),
    ]
    cp = sum(value for _, value, _ in components)
    counts = {
        "passed": sum(1 for o in tests.values() if o in ok),
        "failed": sum(1 for o in tests.values() if o not in ok),
        "total": len(tests),
    }
    return {
        "cp": cp,
        "components": [{"name": n, "value": v, "items": items} for n, v, items in components],
        "tests": counts,
        "lint": facts.get("lint"),
        "types": facts.get("types"),
        "broken": facts["broken"],
        "note": facts.get("note", ""),
    }


def evaluate_position(workdir: Path, tree: str, setup: dict[str, Any], settings: EvalSettings) -> dict[str, Any]:
    """Measure and score the checkout at ``tree`` (the caller guarantees the checkout matches)."""
    facts = measure(workdir, settings)
    base_tree = setup["base_tree"]
    changed = scratch.numstat(workdir, base_tree, tree) if tree != base_tree else {}
    protected = setup["protected"]
    current_asserts = {p: assert_lines(_read(workdir / p)) for p in changed if p in protected}
    base = setup.get("base_facts") or facts
    result = score(
        facts, base, changed=changed, protected=protected, current_asserts=current_asserts, settings=settings
    )
    result["tree"] = tree
    result["facts"] = {
        "tests": facts["tests"],
        "broken": facts["broken"],
        "lint": facts["lint"],
        "types": facts["types"],
    }
    return result


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
