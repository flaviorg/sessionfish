"""Run a full review: play the game, annotate it, analyse the critical plies, freeze puzzles.

This is the only place that wires the two graphs together. Everything it decides (which plies
are critical, which line is best, whether a rule is verified) is code.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.types import Command
from pydantic import ValidationError

from sessionfish import __version__, scratch
from sessionfish.analysis import AnalysisDeps, build_analysis_graph, check_rule
from sessionfish.annotate import Thresholds, accuracy, annotate, critical_plies, summary
from sessionfish.evaluate import EvalSettings
from sessionfish.game import build_game_graph, main_line, run_config
from sessionfish.llm.port import LLM
from sessionfish.redact import redact, redact_obj
from sessionfish.transcript import Session, parse_lines, parse_session, prefix_before

AnswerFn = Callable[[dict[str, Any]], dict[str, Any]]
Event = Callable[[str, dict[str, Any]], None]


@dataclass
class ReviewOptions:
    settings: EvalSettings = field(default_factory=EvalSettings)
    thresholds: Thresholds = field(default_factory=Thresholds)
    analyse: bool = True
    max_critical: int = 3
    line_timeout: float = 180.0
    answer: AnswerFn | None = None  # answers rule interrupts; None skips rule review
    on_event: Event | None = None


@dataclass
class Baseline:
    sha: str
    patch: str = ""

    @classmethod
    def load(cls, path: Path) -> Baseline:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(sha=data["sha"], patch=data.get("patch", ""))


def ply_entries(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "ply": p.index,
            "move": p.move,
            "problem": p.problem,
            "errored": p.errored,
            "tokens": p.tokens,
            "tool": p.tool,
            "label": _label(p.move, p.tool, p.raw_input),
        }
        for p in session.plies
    ]


def _label(move: dict[str, Any] | None, tool: str, raw: dict[str, Any]) -> str:
    from sessionfish.moves import describe

    return describe(move) if move else describe({"tool": tool, **raw})


def play_game(
    session: Session, repo: Path, baseline: Baseline, work: Path, settings: EvalSettings, thread_id: str
) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    """Run the GAME graph over every ply; returns (app, thread config, raw record)."""
    moves = ply_entries(session)
    app = build_game_graph(settings)
    cfg = run_config(thread_id, len(moves))
    app.invoke(
        {
            "source": {
                "repo": str(repo.resolve()),
                "sha": baseline.sha,
                "patch": baseline.patch,
                "scratch_root": str(work / "scratch" / "main"),
            },
            "moves": moves,
            "cursor": 0,
            "line": "main",
        },
        cfg,
    )
    return app, cfg, main_line(app, cfg, settings.weights)


def _files_before(record: list[dict[str, Any]], root: Path) -> Callable[[int], dict[str, str]]:
    def files(ply: int) -> dict[str, str]:
        tree = record[ply - 1]["tree"]
        paths: list[str] = []
        for r in record[1 : ply + 1]:
            path = (r.get("move") or {}).get("file_path")
            if path and path not in paths:
                paths.append(path)
        out = {}
        for path in paths[-6:]:
            text = scratch.show(root, tree, path)
            if text is not None:
                out[path] = text
        return out

    return files


def _diffs(record: list[dict[str, Any]], root: Path) -> None:
    for prev, cur in itertools.pairwise(record):
        cur["diff"] = scratch.diff(root, prev["tree"], cur["tree"]) if prev["tree"] != cur["tree"] else ""


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in entry.items() if k != "checkpoint"}
    pos = dict(out.get("position") or {})
    pos.pop("facts", None)
    out["position"] = pos
    return out


def review(
    transcript: Path,
    repo: Path,
    baseline: Baseline,
    out_dir: Path,
    llm: LLM,
    options: ReviewOptions | None = None,
) -> dict[str, Any]:
    opts = options or ReviewOptions()
    emit = opts.on_event or (lambda kind, data: None)
    session = parse_session(transcript)
    run_dir = out_dir.resolve() / _slug(session.session_id)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    emit("parsed", {"session": session.session_id, "plies": len(session.plies), "skipped": dict(session.skipped_tools)})

    app, thread, raw = play_game(session, repo, baseline, run_dir, opts.settings, f"game-{session.session_id}")
    record = annotate(raw, opts.thresholds)
    root = run_dir / "scratch" / "main"
    _diffs(record, root)
    emit("played", {"record": record})

    analyses: list[dict[str, Any]] = []
    puzzles: list[dict[str, Any]] = []
    critical = critical_plies(record, opts.max_critical) if opts.analyse else []
    if critical:
        puzzle_dir = run_dir / "puzzles"

        def make_puzzle(ply: int, best: dict[str, Any] | None) -> dict[str, Any]:
            puzzle = freeze(session, record, ply, baseline, best, opts)
            puzzle_dir.mkdir(exist_ok=True)
            prefix = [redact(line) for line in prefix_before(session, session.plies[ply - 1])]
            (puzzle_dir / f"{puzzle['id']}.prefix.jsonl").write_text("\n".join(prefix) + "\n", encoding="utf-8")
            (puzzle_dir / f"{puzzle['id']}.json").write_text(json.dumps(redact_obj(puzzle), indent=2) + "\n", "utf-8")
            puzzles.append(puzzle)
            return puzzle

        deps = AnalysisDeps(
            game=app,
            thread=thread,
            llm=llm,
            task=session.task,
            record=record,
            files_before=_files_before(record, root),
            line_dir=lambda label: run_dir / "scratch" / label,
            make_puzzle=make_puzzle,
            thresholds=opts.thresholds,
            weights=opts.settings.weights,
            line_timeout=opts.line_timeout,
        )
        graph = build_analysis_graph(deps)
        for crit in critical:
            emit("analysing", {"ply": crit["ply"], "glyph": crit["glyph"]})
            result = asyncio.run(_analyse(graph, crit, opts.answer, emit))
            analyses.append(result)
            emit("analysed", result)

    report = {
        "sessionfish": __version__,
        "session": {
            "id": session.session_id,
            "task": session.task,
            "cwd": session.cwd,
            "plies": len(session.plies),
            "skipped_tools": dict(session.skipped_tools),
            "tokens": session.total_tokens,
        },
        "base": {"sha": baseline.sha, "dirty": bool(baseline.patch.strip())},
        "scope": list(opts.settings.scope),
        "thresholds": vars(opts.thresholds),
        "summary": summary(record),
        "accuracy": accuracy(record),
        "positions": {"plies": len(record) - 1, "distinct": len({r["tree"] for r in record})},
        "record": [_public(r) for r in record],
        "analysis": analyses,
        "puzzles": [p["id"] for p in puzzles],
        "model": llm.name,
    }
    report = redact_obj(report)
    assert isinstance(report, dict)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    from sessionfish.report import write_markdown
    from sessionfish.viewer import write_viewer

    write_markdown(report, run_dir / "report.md")
    write_viewer(report, run_dir / "index.html")
    shutil.rmtree(run_dir / "scratch", ignore_errors=True)
    report["paths"] = {"dir": str(run_dir), "html": str(run_dir / "index.html"), "md": str(run_dir / "report.md")}
    return report


async def _analyse(graph: Any, crit: dict[str, Any], answer: AnswerFn | None, emit: Event) -> dict[str, Any]:
    cfg = {"configurable": {"thread_id": f"analysis-ply-{crit['ply']}"}}
    state = await graph.ainvoke({"ply": crit["ply"], "severity": crit["severity"], "results": [], "tokens": 0}, cfg)
    emit("lines", {**state, "results": sorted(state.get("results") or [], key=lambda r: r["n"])})
    while state.get("__interrupt__"):
        payload = state["__interrupt__"][0].value
        emit("rule_proposed", payload)
        decision = answer(payload) if answer else {"accept": False, "note": "rule review skipped (--rules skip)"}
        try:
            state = await graph.ainvoke(Command(resume=decision), cfg)
        except ValidationError as exc:  # the answer does not fit RuleDecision: treat it as a rejection
            decision = {"accept": False, "note": f"invalid answer: {str(exc).splitlines()[0][:160]}"}
            state = await graph.ainvoke(Command(resume=decision), cfg)
        emit("rule_decided", decision)
    keys = ("ply", "severity", "proposals", "proposal_error", "results", "best", "puzzle", "rule", "decision")
    out = {k: state.get(k) for k in keys}
    out["rule_check"] = state.get("rule_check")
    out["glyph"] = crit["glyph"]
    out["results"] = sorted(out["results"] or [], key=lambda r: r["n"])
    out["tokens"] = state.get("tokens", 0)
    return out


def freeze(
    session: Session,
    record: list[dict[str, Any]],
    ply: int,
    baseline: Baseline,
    best: dict[str, Any] | None,
    opts: ReviewOptions,
) -> dict[str, Any]:
    """A reproducible puzzle: base sha + dirty patch + transcript prefix -> this exact tree and eval."""
    before, played = record[ply - 1], record[ply]
    pid = f"{_slug(session.session_id)[:8]}-ply{ply}"
    return {
        "id": pid,
        "session_id": session.session_id,
        "ply": ply,
        "task": session.task,
        "base_sha": baseline.sha,
        "patch": baseline.patch,
        "patch_sha256": hashlib.sha256(baseline.patch.encode()).hexdigest(),
        "prefix": f"{pid}.prefix.jsonl",
        "tree_before": before["tree"],
        "eval_before": {"score": before["score"], "wc": before["wc"]},
        "played": {
            "move": played["move"],
            "score": played["score"],
            "glyph": played["glyph"],
            "motif": played["motif"],
        },
        "best_line": {"idea": best["idea"], "moves": best["moves"], "final_score": best["final_score"]}
        if best
        else None,
        "scope": list(opts.settings.scope),
        "settings_digest": opts.settings.digest(),
    }


def verify_puzzle(
    puzzle_path: Path, repo: Path, rule: str, llm: LLM, work: Path, settings: EvalSettings, thresholds: Thresholds
) -> dict[str, Any]:
    """Rebuild a puzzle position from scratch and check a rule against only that position."""
    work = work.resolve()
    puzzle = json.loads(puzzle_path.read_text(encoding="utf-8"))
    prefix_lines = (puzzle_path.parent / puzzle["prefix"]).read_text(encoding="utf-8").splitlines()
    session = parse_lines(prefix_lines, default_id=puzzle["session_id"])
    if work.exists():
        shutil.rmtree(work)
    baseline = Baseline(puzzle["base_sha"], puzzle.get("patch", ""))
    app, thread, raw = play_game(session, repo, baseline, work, settings, f"puzzle-{puzzle['id']}")
    record = annotate(raw, thresholds)
    position = record[-1]  # the prefix ends right before the blundered ply
    deps = AnalysisDeps(
        game=app,
        thread=thread,
        llm=llm,
        task=puzzle.get("task") or session.task,
        record=record,
        files_before=_files_before(record, work / "scratch" / "main"),
        line_dir=lambda label: work / "scratch" / label,
        make_puzzle=lambda ply, best: {},
        thresholds=thresholds,
        weights=settings.weights,
    )
    check = asyncio.run(check_rule(deps, len(record), rule))
    shutil.rmtree(work / "scratch", ignore_errors=True)
    return {
        "puzzle": puzzle["id"],
        "reproduced": position["tree"] == puzzle["tree_before"],
        "tree": position["tree"],
        "expected_tree": puzzle["tree_before"],
        "eval_before": position["score"],
        "expected_eval": puzzle["eval_before"]["score"],
        **check,
    }


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text)[:64] or "session"
