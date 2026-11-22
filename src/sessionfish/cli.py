"""Command line: ``review``, ``demo``, ``hook``, ``puzzle verify``, ``graph``, ``version``."""

from __future__ import annotations

import json
import shutil
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from sessionfish import __version__, scratch
from sessionfish.annotate import Thresholds
from sessionfish.evaluate import EvalSettings
from sessionfish.llm.factory import make_llm, resolve_model
from sessionfish.llm.port import LLMError
from sessionfish.moves import RuleDecision
from sessionfish.transcript import TranscriptError, parse_session

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Chess-engine-style game review for coding-agent sessions.",
)
hook_app = typer.Typer(no_args_is_help=True, help="Claude Code hook that records the base of each session.")
puzzle_app = typer.Typer(no_args_is_help=True, help="Frozen pre-blunder positions.")
app.add_typer(hook_app, name="hook")
app.add_typer(puzzle_app, name="puzzle")

DEMO_SESSION = "weakened-assert"
DEFAULT_OUT = Path(".sessionfish/reviews")


class RulePolicy(StrEnum):
    ask = "ask"
    accept = "accept"
    skip = "skip"


class FailOn(StrEnum):
    never = "never"
    blunder = "blunder"
    mistake = "mistake"
    inaccuracy = "inaccuracy"


def find_examples() -> Path:
    """Repo checkout: ./examples. Installed wheel: sessionfish/_examples."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "examples", here.parent / "_examples"):
        if (candidate / "discount_shop").is_dir():
            return candidate
    raise typer.BadParameter("demo fixtures not found; reinstall sessionfish")


def make_answer(policy: RulePolicy, console: Console):
    if policy is RulePolicy.skip:
        return None
    if policy is RulePolicy.accept:
        return lambda payload: RuleDecision(accept=True, note="auto-accepted (--rules accept)").model_dump()

    def ask(payload: dict[str, Any]) -> dict[str, Any]:
        console.print(f"\n[bold]Rule proposed for ply {payload['ply']}[/]: {payload['rule']}")
        console.print(f"[dim]{payload.get('rationale', '')}[/]")
        if not sys.stdin.isatty():
            return RuleDecision(accept=False, note="stdin is not a terminal").model_dump()
        choice = typer.prompt("Accept, edit or reject? [a/e/r]", default="a").strip().lower()
        if choice.startswith("e"):
            text = typer.prompt("Rule text", default=payload["rule"])
            return RuleDecision(accept=True, rule=text).model_dump()
        return RuleDecision(accept=choice.startswith("a")).model_dump()

    return ask


def _settings(scope: list[str] | None, python: str | None, lint: bool, types: bool) -> EvalSettings:
    return EvalSettings(python=python or sys.executable, scope=tuple(scope or ()), lint=lint, types=types)


def _run(
    transcript: Path,
    repo: Path,
    baseline: Any,
    out: Path,
    model: str | None,
    settings: EvalSettings,
    thresholds: Thresholds,
    analyse: bool,
    rules: RulePolicy,
    max_critical: int,
    line_timeout: float,
    as_json: bool,
) -> dict[str, Any]:
    from sessionfish.review import ReviewOptions, review
    from sessionfish.ui import Renderer

    console = Console(stderr=as_json)
    ui = Renderer(console)
    llm = make_llm(resolve_model(model))
    options = ReviewOptions(
        settings=settings,
        thresholds=thresholds,
        analyse=analyse,
        max_critical=max_critical,
        line_timeout=line_timeout,
        answer=make_answer(rules, console),
        on_event=ui.event,
    )
    report = review(transcript, repo, baseline, out, llm, options)
    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        ui.final(report)
    return report


def _exit_code(report: dict[str, Any], fail_on: FailOn) -> int:
    order = ["blunder", "mistake", "inaccuracy"]
    if fail_on is FailOn.never:
        return 0
    worst = order[: order.index(fail_on.value) + 1]
    return 1 if any(report["summary"][k] for k in worst) else 0


@app.command()
def review(
    transcript: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Claude Code session .jsonl")],
    repo: Annotated[Path, typer.Option(help="Repo the session ran in.")] = Path("."),
    baseline: Annotated[Path | None, typer.Option(help="Baseline JSON from the SessionStart hook.")] = None,
    base: Annotated[str | None, typer.Option(help="Base commit when no baseline was recorded.")] = None,
    scope: Annotated[list[str] | None, typer.Option(help="Glob of files the task may touch (repeatable).")] = None,
    out: Annotated[Path, typer.Option(help="Where reviews are written.")] = DEFAULT_OUT,
    model: Annotated[str | None, typer.Option(help="provider:model for analysis, or 'fake'.")] = None,
    analysis: Annotated[bool, typer.Option("--analysis/--no-analysis", help="Fork and replay alternatives.")] = True,
    rules: Annotated[RulePolicy, typer.Option(help="Answer rule proposals: ask, accept or skip.")] = RulePolicy.ask,
    python: Annotated[str | None, typer.Option(help="Interpreter that runs the repo's tests.")] = None,
    lint: Annotated[bool, typer.Option("--lint/--no-lint", help="Count ruff errors.")] = True,
    types: Annotated[bool, typer.Option("--types/--no-types", help="Count mypy errors (needs mypy).")] = False,
    max_critical: Annotated[int, typer.Option(min=0, max=3, help="Critical plies to analyse.")] = 3,
    line_timeout: Annotated[float, typer.Option(help="Seconds before an alternative line is abandoned.")] = 180.0,
    recovery_window: Annotated[int, typer.Option(min=0, help="Plies a drop has to recover.")] = 2,
    fail_on: Annotated[FailOn, typer.Option(help="Exit 1 if the review finds this or worse.")] = FailOn.never,
    as_json: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Replay a session, put an eval on every move and analyse the worst ones."""
    from sessionfish.hook import find_baseline
    from sessionfish.review import Baseline

    console = Console(stderr=True)
    try:
        session = parse_session(transcript)
        repo = repo.resolve()
        found = baseline or find_baseline(repo, session.session_id)
        if found:
            bl = Baseline.load(found)
        else:
            bl = Baseline(scratch.resolve_sha(repo, base or "HEAD"))
            if not base:
                console.print(
                    "[yellow]No baseline recorded for this session; replaying from HEAD. Install the hook "
                    "(sessionfish hook install) so the exact starting point is saved.[/]"
                )
        report = _run(
            transcript,
            repo,
            bl,
            out,
            model,
            _settings(scope, python, lint, types),
            Thresholds(recovery_window=recovery_window),
            analysis,
            rules,
            max_critical,
            line_timeout,
            as_json,
        )
    except (TranscriptError, scratch.GitError, LLMError, OSError) as exc:
        console.print(f"[bold red]error:[/] {exc}")
        raise typer.Exit(2) from exc
    raise typer.Exit(_exit_code(report, fail_on))


@app.command()
def demo(
    out: Annotated[Path, typer.Option(help="Where the demo repo and review are written.")] = Path(".sessionfish/demo"),
    rules: Annotated[
        RulePolicy, typer.Option(help="Answer the rule proposal: accept, ask or skip.")
    ] = RulePolicy.accept,
    as_json: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Review the bundled session offline with the scripted model (no API key, no network)."""
    from sessionfish.review import Baseline

    examples = find_examples()
    meta = json.loads((examples / "sessions" / f"{DEMO_SESSION}.json").read_text(encoding="utf-8"))
    repo = out / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(examples / "discount_shop", repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sha = scratch.init_fixture_repo(repo)
    _run(
        examples / "sessions" / f"{DEMO_SESSION}.jsonl",
        repo,
        Baseline(sha),
        out / "reviews",
        "fake",
        _settings(meta["scope"], None, True, False),
        Thresholds(),
        True,
        rules,
        3,
        120.0,
        as_json,
    )


@hook_app.command("session-start")
def hook_session_start() -> None:
    """Read the SessionStart payload on stdin and save HEAD plus the dirty patch."""
    from sessionfish.hook import record_baseline

    try:
        payload = json.loads(sys.stdin.read() or "{}")
        path = record_baseline(payload)
        print(f"sessionfish: baseline saved to {path}", file=sys.stderr)
    except Exception as exc:  # a hook must never break the session
        print(f"sessionfish: baseline not recorded: {exc}", file=sys.stderr)


@hook_app.command("install")
def hook_install() -> None:
    """Print the settings.json snippet that enables the hook (nothing is written)."""
    from sessionfish.hook import SETTINGS_SNIPPET

    typer.echo("Add this to .claude/settings.json (project) or ~/.claude/settings.json (all projects):\n")
    typer.echo(json.dumps(SETTINGS_SNIPPET, indent=2))


@puzzle_app.command("verify")
def puzzle_verify(
    puzzle: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="puzzles/<id>.json from a review")],
    rule: Annotated[str, typer.Option(help="The CLAUDE.md rule to test against this position.")],
    repo: Annotated[Path, typer.Option(help="Repo that contains the puzzle's base sha.")] = Path("."),
    model: Annotated[str | None, typer.Option(help="provider:model, or 'fake'.")] = None,
    scope: Annotated[list[str] | None, typer.Option(help="Override the puzzle's scope.")] = None,
    work: Annotated[Path, typer.Option(help="Scratch directory.")] = Path(".sessionfish/puzzle-work"),
) -> None:
    """Rebuild a puzzle from sha + transcript prefix and replay only that position with a rule."""
    from sessionfish.review import verify_puzzle

    data = json.loads(puzzle.read_text(encoding="utf-8"))
    settings = _settings(scope or data.get("scope"), None, True, False)
    try:
        result = verify_puzzle(
            puzzle, repo.resolve(), rule, make_llm(resolve_model(model)), work, settings, Thresholds()
        )
    except (scratch.GitError, LLMError, OSError) as exc:
        Console(stderr=True).print(f"[bold red]error:[/] {exc}")
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(result, indent=2))
    raise typer.Exit(0 if result["verdict"] == "verified" and result["reproduced"] else 1)


@app.command()
def graph() -> None:
    """Print both graphs as Mermaid."""
    from sessionfish.graphs import mermaid

    typer.echo(mermaid())


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)
