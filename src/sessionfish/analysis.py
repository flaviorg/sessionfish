"""The ANALYSIS graph: what should have been played at a critical ply.

    START -> propose_alternatives --Send x<=3--> play_line --(defer)--> pick_best
          -> [blunder] freeze_puzzle -> propose_rule -> review_rule (interrupt) -> verify_rule -> END

One thread per critical ply. ``propose_alternatives`` is the only creative step: the model
proposes up to three lines (same tool vocabulary as the session, schema-validated, at most five
moves each). Each line is a ``Send`` to ``play_line``, which forks the GAME thread at the
checkpoint before the critical ply (``update_state(..., as_node="eval_position")``) and replays
the line with the same ``apply_move``/``eval_position`` nodes and the same eval cache.
``play_line`` is async with a timeout; a line that hangs or cannot be applied lands in an error
handler instead of failing the run. ``pick_best`` is a deferred fan-in: it runs once, after all
lines, and picks by eval, in code.

For a blunder, the position is frozen as a puzzle and the model proposes a CLAUDE.md rule. The
rule goes to a human through ``interrupt(response_schema=RuleDecision)``. An accepted rule is
verified by replaying only that position twice, with and without the rule in context.
"""

from __future__ import annotations

import asyncio
import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeError, NodeTimeoutError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt
from pydantic import ValidationError

from sessionfish import prompts
from sessionfish.annotate import Thresholds, annotate
from sessionfish.evaluate import Weights
from sessionfish.game import fork, line_from_fork
from sessionfish.llm.port import LLM, LLMError
from sessionfish.moves import Alternatives, Line, RuleDecision, RuleProposal, describe


class AnalysisState(TypedDict, total=False):
    ply: int
    severity: str
    proposals: list[dict[str, Any]]
    proposal_error: str | None
    results: Annotated[list[dict[str, Any]], operator.add]
    best: dict[str, Any] | None
    puzzle: dict[str, Any] | None
    rule: dict[str, Any] | None
    decision: dict[str, Any] | None
    rule_check: dict[str, Any] | None
    tokens: Annotated[int, operator.add]


@dataclass
class AnalysisDeps:
    game: Any
    thread: dict[str, Any]
    llm: LLM
    task: str
    record: list[dict[str, Any]]  # the annotated main line
    files_before: Callable[[int], dict[str, str]]  # file snapshots the model sees before a ply
    line_dir: Callable[[str], Path]
    make_puzzle: Callable[[int, dict[str, Any] | None], dict[str, Any]]
    thresholds: Thresholds = field(default_factory=Thresholds)
    weights: Weights = field(default_factory=Weights)
    line_timeout: float = 180.0


# --------------------------------------------------------------------------- replaying lines


async def play_moves(deps: AnalysisDeps, ply: int, moves: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    """Fork the game before ``ply``, play ``moves``, return the annotated line (fork point first)."""
    at = deps.record[ply - 1]["checkpoint"]
    entries = [
        {"ply": ply + i, "move": m, "tool": m["tool"], "label": describe(m), "tokens": 0} for i, m in enumerate(moves)
    ]
    cfg = fork(deps.game, at, entries, workdir=deps.line_dir(label), line=label)
    await deps.game.ainvoke(None, {**cfg, "recursion_limit": 2 * len(entries) + 10})
    line = line_from_fork(deps.game, deps.thread, cfg, deps.weights)
    return annotate(line, deps.thresholds)


def line_summary(annotated: list[dict[str, Any]]) -> dict[str, Any]:
    final = annotated[-1]
    return {
        "final_score": final["score"],
        "final_wc": final["wc"],
        "plies": [
            {
                "ply": r["ply"],
                "label": r["label"],
                "move": r["move"],
                "status": r["status"],
                "score": r["score"],
                "wc": r["wc"],
                "glyph": r["glyph"],
                "tests": r["position"]["tests"],
            }
            for r in annotated[1:]
        ],
        "blunders": sum(1 for r in annotated[1:] if r["severity"] == "blunder"),
    }


async def check_rule(deps: AnalysisDeps, ply: int, rule: str) -> dict[str, Any]:
    """Replay only the position before ``ply``: the model continues with and without the rule."""
    files = deps.files_before(ply)

    async def attempt(text: str, label: str) -> dict[str, Any]:
        prompt = prompts.continue_prompt(deps.task, deps.record, ply, files, text)
        reply = await asyncio.to_thread(deps.llm.structured, prompts.CONTINUE_SYSTEM, prompt, Line)
        moves = [m.model_dump() for m in reply.value.moves]
        try:
            summary = line_summary(await play_moves(deps, ply, moves, label))
        except Exception as exc:  # a continuation that cannot be replayed solves nothing
            summary = {"error": f"{type(exc).__name__}: {exc}", "blunders": 1, "final_wc": -1.0, "plies": []}
        return {"idea": reply.value.idea, "moves": moves, "tokens": reply.tokens, **summary}

    with_rule, control = await asyncio.gather(attempt(rule, f"rule-{ply}"), attempt("", f"control-{ply}"))
    if with_rule["blunders"]:
        verdict = "failed"  # the rule was in context and the position was still blundered
    elif control["blunders"]:
        verdict = "verified"  # without the rule: blunder; with it: no blunder
    else:
        verdict = "not_needed"  # the model avoids the blunder even without the rule
    return {"rule": rule, "verdict": verdict, "with_rule": with_rule, "control": control}


# --------------------------------------------------------------------------- the graph


def build_analysis_graph(deps: AnalysisDeps, *, checkpointer: Any | None = None) -> Any:
    t = deps.thresholds

    def propose_alternatives(state: AnalysisState) -> dict[str, Any]:
        ply = state["ply"]
        prompt = prompts.alternatives_prompt(deps.task, deps.record, ply, deps.files_before(ply))
        try:
            reply = deps.llm.structured(prompts.ALTERNATIVES_SYSTEM, prompt, Alternatives)
        except (LLMError, ValidationError) as exc:
            return {"proposals": [], "proposal_error": str(exc)[:500]}
        return {"proposals": [line.model_dump() for line in reply.value.lines], "tokens": reply.tokens}

    def fan_out(state: AnalysisState) -> list[Send] | str:
        sends = [
            Send("play_line", {"ply": state["ply"], "n": n, "line": line})
            for n, line in enumerate(state.get("proposals") or [], start=1)
        ]
        return sends or "pick_best"

    async def play_line(arg: dict[str, Any]) -> dict[str, Any]:
        line, n, ply = arg["line"], arg["n"], arg["ply"]
        annotated = await play_moves(deps, ply, line["moves"], f"alt-{ply}-{n}")
        return {
            "results": [
                {"n": n, "idea": line["idea"], "moves": line["moves"], "status": "ok", **line_summary(annotated)}
            ]
        }

    def line_failed(arg: dict[str, Any], error: NodeError) -> dict[str, Any]:
        timed_out = isinstance(error.error, NodeTimeoutError)
        return {
            "results": [
                {
                    "n": arg["n"],
                    "idea": arg["line"]["idea"],
                    "moves": arg["line"]["moves"],
                    "status": "timeout" if timed_out else "failed",
                    "error": f"{type(error.error).__name__}: {error.error}"[:300],
                }
            ]
        }

    def pick_best(state: AnalysisState) -> dict[str, Any]:
        ply = state["ply"]
        record = deps.record
        window = record[ply : min(len(record), ply + t.recovery_window + 1)]
        played_wc = max(r["wc"] for r in window)
        ok = [r for r in state.get("results", []) if r["status"] == "ok"]
        if not ok:
            return {"best": None}
        best = max(ok, key=lambda r: (r["final_wc"], -len(r["moves"]), -r["n"]))
        gain = round(best["final_wc"] - played_wc, 4)
        return {"best": {**best, "played_wc": played_wc, "gain": gain, "better": gain >= t.inaccuracy}}

    def after_pick(state: AnalysisState) -> str:
        return "freeze_puzzle" if state.get("severity") == "blunder" else END

    def freeze_puzzle(state: AnalysisState) -> dict[str, Any]:
        return {"puzzle": deps.make_puzzle(state["ply"], state.get("best"))}

    def propose_rule(state: AnalysisState) -> dict[str, Any]:
        ply = state["ply"]
        prompt = prompts.rule_prompt(deps.task, deps.record, ply, deps.files_before(ply), state.get("best"))
        try:
            reply = deps.llm.structured(prompts.RULE_SYSTEM, prompt, RuleProposal)
        except (LLMError, ValidationError) as exc:
            return {"rule": None, "proposal_error": str(exc)[:500]}
        return {"rule": reply.value.model_dump(), "tokens": reply.tokens}

    def review_rule(state: AnalysisState) -> dict[str, Any]:
        rule = state.get("rule")
        if not rule:
            return {"decision": {"accept": False, "note": "no rule was proposed"}}
        answer = interrupt(
            {"ply": state["ply"], "puzzle": (state.get("puzzle") or {}).get("id"), **rule},
            response_schema=RuleDecision,
        )
        # LangGraph validates the resume value against response_schema before we get here
        return {"decision": RuleDecision.model_validate(answer).model_dump()}

    def after_review(state: AnalysisState) -> str:
        return "verify_rule" if (state.get("decision") or {}).get("accept") else END

    async def verify_rule(state: AnalysisState) -> dict[str, Any]:
        text = (state["decision"] or {}).get("rule") or state["rule"]["rule"]
        return {"rule_check": await check_rule(deps, state["ply"], text)}

    def rule_check_failed(state: AnalysisState, error: NodeError) -> dict[str, Any]:
        kind = "timeout" if isinstance(error.error, NodeTimeoutError) else "error"
        return {"rule_check": {"verdict": kind, "error": f"{type(error.error).__name__}: {error.error}"[:300]}}

    builder = StateGraph(AnalysisState)
    builder.add_node("propose_alternatives", propose_alternatives)
    builder.add_node("play_line", play_line, timeout=deps.line_timeout, error_handler=line_failed)
    builder.add_node("pick_best", pick_best, defer=True)
    builder.add_node("freeze_puzzle", freeze_puzzle)
    builder.add_node("propose_rule", propose_rule)
    builder.add_node("review_rule", review_rule)
    builder.add_node("verify_rule", verify_rule, timeout=2 * deps.line_timeout, error_handler=rule_check_failed)
    builder.add_edge(START, "propose_alternatives")
    builder.add_conditional_edges("propose_alternatives", fan_out, ["play_line", "pick_best"])
    builder.add_edge("play_line", "pick_best")
    builder.add_conditional_edges("pick_best", after_pick, ["freeze_puzzle", END])
    builder.add_edge("freeze_puzzle", "propose_rule")
    builder.add_edge("propose_rule", "review_rule")
    builder.add_conditional_edges("review_rule", after_review, ["verify_rule", END])
    builder.add_edge("verify_rule", END)
    return builder.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver(), name="analysis")
