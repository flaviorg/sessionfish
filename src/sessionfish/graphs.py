"""Both graphs as Mermaid, generated from the compiled graphs (used by docs and a drift test)."""

from __future__ import annotations

from pathlib import Path

from sessionfish.analysis import AnalysisDeps, build_analysis_graph
from sessionfish.evaluate import EvalSettings
from sessionfish.game import build_game_graph
from sessionfish.llm.fake import ScriptedLLM


def compiled() -> tuple[object, object]:
    game = build_game_graph(EvalSettings())
    deps = AnalysisDeps(
        game=game,
        thread={},
        llm=ScriptedLLM(),
        task="",
        record=[],
        files_before=lambda ply: {},
        line_dir=lambda label: Path(label),
        make_puzzle=lambda ply, best: {},
    )
    return game, build_analysis_graph(deps)


def mermaid() -> str:
    game, analysis = compiled()
    return (
        "%% GAME graph\n"
        + game.get_graph().draw_mermaid()  # type: ignore[attr-defined]
        + "\n%% ANALYSIS graph\n"
        + analysis.get_graph().draw_mermaid()  # type: ignore[attr-defined]
    )
