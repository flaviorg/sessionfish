"""ANALYSIS graph mechanics with the game replay stubbed out: timeouts, errors, interrupts."""

import asyncio
from pathlib import Path

import pytest
from langgraph.types import Command
from pydantic import ValidationError

from sessionfish import analysis
from sessionfish.analysis import AnalysisDeps, build_analysis_graph
from sessionfish.llm.fake import Rule, ScriptedLLM
from sessionfish.llm.port import LLMError

MOVE = {"tool": "Bash", "command": "pytest"}


def entry(ply, score, wc, severity=None):
    pos = {
        "tests": {"passed": 1, "failed": 0, "total": 1},
        "components": [{"name": "regressed", "value": score, "items": []}],
    }
    return {
        "ply": ply,
        "score": score,
        "wc": wc,
        "severity": severity,
        "glyph": "??" if severity else "",
        "move": MOVE,
        "status": "applied",
        "label": "Bash pytest",
        "position": pos,
        "checkpoint": {},
        "tree": "t",
    }


RECORD = [entry(0, 0, 0.0), entry(1, 0, 0.0), entry(2, -400, -0.66, "blunder"), entry(3, -400, -0.66)]
LINES = {"lines": [{"idea": f"line {i}", "moves": [MOVE]} for i in (1, 2, 3)]}


def deps(llm=None, timeout=5.0):
    return AnalysisDeps(
        game=None,
        thread={},
        llm=llm or ScriptedLLM([Rule("alternatives", LINES), Rule("rule", {"rule": "R", "rationale": "x"})]),
        task="t",
        record=RECORD,
        files_before=lambda ply: {},
        line_dir=lambda label: Path(label),
        make_puzzle=lambda ply, best: {"id": f"p{ply}", "best": best},
        line_timeout=timeout,
    )


def fake_line(final_wc):
    return [
        {
            "ply": 1,
            "score": 0,
            "wc": 0.0,
            "label": "start",
            "move": None,
            "status": "start",
            "glyph": "",
            "severity": None,
            "position": {"tests": {}},
        },
        {
            "ply": 2,
            "score": int(final_wc * 500),
            "wc": final_wc,
            "label": "m",
            "move": MOVE,
            "status": "noop",
            "glyph": "",
            "severity": None,
            "position": {"tests": {}},
        },
    ]


def run(graph, answer=None, thread="x"):
    async def go():
        cfg = {"configurable": {"thread_id": thread}}
        state = await graph.ainvoke({"ply": 2, "severity": "blunder", "results": [], "tokens": 0}, cfg)
        interrupts = []
        while state.get("__interrupt__"):
            interrupts.append(state["__interrupt__"][0])
            state = await graph.ainvoke(Command(resume=answer), cfg)
        return state, interrupts

    return asyncio.run(go())


def test_hung_line_times_out_into_the_error_handler(monkeypatch):
    async def play(d, ply, moves, label):
        if label.endswith("-2"):
            await asyncio.sleep(30)  # a replay that hangs
        if label.endswith("-3"):
            raise RuntimeError("replay exploded")
        return fake_line(0.4)

    monkeypatch.setattr(analysis, "play_moves", play)
    state, _ = run(build_analysis_graph(deps(timeout=0.3)), answer={"accept": False})
    by_n = {r["n"]: r for r in state["results"]}
    assert by_n[1]["status"] == "ok"
    assert by_n[2]["status"] == "timeout" and "NodeTimeoutError" in by_n[2]["error"]
    assert by_n[3]["status"] == "failed" and "replay exploded" in by_n[3]["error"]
    assert state["best"]["n"] == 1  # the deferred fan-in ran once, after every line finished
    assert state["best"]["better"]


def test_rule_interrupt_carries_the_response_schema_and_reject_skips_verification(monkeypatch):
    async def play(d, ply, moves, label):
        return fake_line(0.1)

    monkeypatch.setattr(analysis, "play_moves", play)
    state, interrupts = run(build_analysis_graph(deps()), answer={"accept": False, "note": "no"})
    assert interrupts[0].value["rule"] == "R"
    assert interrupts[0].response_schema["required"] == ["accept"]
    assert state["decision"]["accept"] is False
    assert state.get("rule_check") is None
    assert state["puzzle"]["id"] == "p2"


def test_langgraph_validates_the_resume_value_against_the_schema(monkeypatch):
    async def play(d, ply, moves, label):
        return fake_line(0.1)

    monkeypatch.setattr(analysis, "play_moves", play)
    graph = build_analysis_graph(deps())

    async def go():
        cfg = {"configurable": {"thread_id": "v"}}
        await graph.ainvoke({"ply": 2, "severity": "blunder", "results": []}, cfg)
        with pytest.raises(ValidationError):
            await graph.ainvoke(Command(resume={"accept": "maybe?"}), cfg)
        # still paused at the same interrupt: a valid answer goes through
        assert graph.get_state(cfg).next == ("review_rule",)
        return await graph.ainvoke(Command(resume={"accept": False, "note": "later"}), cfg)

    state = asyncio.run(go())
    assert state["decision"] == {"accept": False, "rule": None, "note": "later"}


def test_model_errors_do_not_fail_the_analysis():
    class Broken:
        name = "broken"

        def structured(self, system, user, schema):
            raise LLMError("provider down")

    state, interrupts = run(build_analysis_graph(deps(llm=Broken())))
    assert state["proposals"] == [] and "provider down" in state["proposal_error"]
    assert state["best"] is None and not interrupts


@pytest.mark.parametrize("severity", ["mistake", "inaccuracy"])
def test_only_blunders_become_puzzles(monkeypatch, severity):
    async def play(d, ply, moves, label):
        return fake_line(0.1)

    monkeypatch.setattr(analysis, "play_moves", play)

    async def go():
        g = build_analysis_graph(deps())
        return await g.ainvoke(
            {"ply": 2, "severity": severity, "results": []}, {"configurable": {"thread_id": severity}}
        )

    state = asyncio.run(go())
    assert state.get("puzzle") is None and not state.get("__interrupt__")
