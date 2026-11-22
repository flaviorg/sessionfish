"""Render both graphs as Mermaid from the compiled graphs and paste them into the docs.

python scripts/gen_graph.py          # rewrite docs/graph.mmd, README.md, docs/architecture.md
python scripts/gen_graph.py --check  # exit 1 if any of them drifted from the code
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from sessionfish.graphs import compiled

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "README.md", ROOT / "docs" / "architecture.md"]


def blocks() -> dict[str, str]:
    game, analysis = compiled()
    return {
        "game": game.get_graph().draw_mermaid().strip(),  # type: ignore[attr-defined]
        "analysis": analysis.get_graph().draw_mermaid().strip(),  # type: ignore[attr-defined]
    }


def pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(<!-- {name}-graph:start -->)\n?.*?\n?(<!-- {name}-graph:end -->)", re.DOTALL)


def render(text: str, graphs: dict[str, str]) -> str:
    for name, mermaid in graphs.items():
        text = pattern(name).sub(lambda m, g=mermaid: f"{m.group(1)}\n```mermaid\n{g}\n```\n{m.group(2)}", text)
    return text


def main(check: bool) -> int:
    graphs = blocks()
    drifted: list[str] = []
    mmd = ROOT / "docs" / "graph.mmd"
    mmd_text = f"%% GAME graph\n{graphs['game']}\n\n%% ANALYSIS graph\n{graphs['analysis']}\n"
    if not mmd.exists() or mmd.read_text() != mmd_text:
        drifted.append("docs/graph.mmd")
        if not check:
            mmd.write_text(mmd_text)
    for path in TARGETS:
        text = path.read_text()
        if not all(pattern(n).search(text) for n in graphs):
            print(f"{path.name}: missing graph markers", file=sys.stderr)
            return 1
        updated = render(text, graphs)
        if updated != text:
            drifted.append(str(path.relative_to(ROOT)))
            if not check:
                path.write_text(updated)
    if check and drifted:
        print("graph diagrams out of date in: " + ", ".join(drifted) + " (run scripts/gen_graph.py)", file=sys.stderr)
        return 1
    print("graph diagrams " + ("up to date" if not drifted else "updated: " + ", ".join(drifted)))
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
