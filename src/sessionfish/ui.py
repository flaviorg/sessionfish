"""Rich terminal rendering. Pure presentation: no decisions happen here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

GLYPH_STYLE = {"blunder": "bold red", "mistake": "bold dark_orange", "inaccuracy": "bold cyan"}
BAR = 16


def rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve())) or "."
    except ValueError:
        return path


def ev(score: int) -> str:
    return f"{score / 100:+.2f}"


def bar(wc: float) -> Text:
    white = round((wc + 1) / 2 * BAR)
    return Text.assemble(("█" * white, "grey93"), ("░" * (BAR - white), "grey42"))


class Renderer:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def event(self, kind: str, data: dict[str, Any]) -> None:
        handler = getattr(self, f"on_{kind}", None)
        if handler:
            handler(data)

    def on_parsed(self, d: dict[str, Any]) -> None:
        skipped = ", ".join(f"{k}×{v}" for k, v in d["skipped"].items()) or "none"
        self.console.print(
            Panel(
                Text.assemble(
                    ("session ", "dim"),
                    d["session"],
                    "\n",
                    ("plies   ", "dim"),
                    str(d["plies"]),
                    ("   other tool calls skipped: ", "dim"),
                    skipped,
                ),
                title=Text("sessionfish", style="bold green"),
                title_align="left",
                border_style="green",
            )
        )

    def on_played(self, d: dict[str, Any]) -> None:
        table = Table(box=None, pad_edge=False, header_style="dim")
        for col, kw in (
            ("ply", {"justify": "right"}),
            ("move", {}),
            ("replay", {}),
            ("tests", {"justify": "right"}),
            ("eval", {"justify": "right"}),
            ("", {}),
            ("", {}),
        ):
            table.add_column(col, **kw)
        for r in d["record"]:
            t = r["position"]["tests"]
            glyph = Text(r["glyph"], style=GLYPH_STYLE.get(r["severity"] or "", ""))
            if r.get("motif"):
                glyph.append(f" {r['motif']}", style="dim")
            status = Text(r["status"], style="dim italic" if r["status"] == "unreplayable" else "dim")
            label = "start position" if r["ply"] == 0 else r["label"]
            table.add_row(
                str(r["ply"]), label[:52], status, f"{t['passed']}/{t['total']}", ev(r["score"]), bar(r["wc"]), glyph
            )
        self.console.print(table)

    def on_analysing(self, d: dict[str, Any]) -> None:
        self.console.print(Text.assemble("\n", ("analysing ply ", "bold"), str(d["ply"]), " ", d["glyph"]))

    def on_rule_proposed(self, d: dict[str, Any]) -> None:
        self.console.print(Text.assemble(("  proposed CLAUDE.md rule: ", "bold magenta"), d["rule"]))

    def on_rule_decided(self, d: dict[str, Any]) -> None:
        verdict = ("accepted", "green") if d.get("accept") else ("not accepted", "yellow")
        self.console.print(Text.assemble(("  rule ", "dim"), verdict, (f"  {d.get('note') or ''}", "dim")))

    def on_lines(self, a: dict[str, Any]) -> None:
        best_n = (a.get("best") or {}).get("n")
        for r in a["results"]:
            if r["status"] == "ok":
                mark = Text(" best", style="bold green") if r["n"] == best_n else Text("")
                self.console.print(
                    Text.assemble(f"  line {r['n']} ", (ev(r["final_score"]), "bold"), mark, f"  {r['idea']}")
                )
            else:
                self.console.print(
                    Text.assemble(
                        f"  line {r['n']} ", (r["status"], "bold red"), f"  {r['idea']}  ", (r.get("error", ""), "dim")
                    )
                )
        if a.get("proposal_error"):
            self.console.print(Text(f"  model error: {a['proposal_error']}", style="red"))
        if a.get("puzzle"):
            self.console.print(Text.assemble(("  puzzle ", "dim"), a["puzzle"]["id"]))

    def on_analysed(self, a: dict[str, Any]) -> None:
        check = a.get("rule_check")
        if check:
            style = "bold green" if check["verdict"] == "verified" else "bold yellow"
            self.console.print(
                Text.assemble(("  rule check: ", "dim"), (check["verdict"], style), _check_detail(check))
            )

    def final(self, report: dict[str, Any]) -> None:
        s = report["summary"]
        line = Text.assemble(
            (f"{s['blunder']} ??", GLYPH_STYLE["blunder"]),
            "  ",
            (f"{s['mistake']} ?", GLYPH_STYLE["mistake"]),
            "  ",
            (f"{s['inaccuracy']} ?!", GLYPH_STYLE["inaccuracy"]),
            "  ",
            (f"{s['unreplayable']} unreplayable", "dim"),
            "   accuracy ",
            (f"{report['accuracy']}%", "bold"),
            ("   positions measured ", "dim"),
            str(report["positions"]["distinct"]),
            (f" of {report['positions']['plies'] + 1}", "dim"),
        )
        paths = report.get("paths", {})
        grid = Table.grid(padding=(0, 2))
        grid.add_row(Text("verdict", style="dim"), line)
        grid.add_row(Text("viewer", style="dim"), rel(paths.get("html", "")))
        grid.add_row(Text("report", style="dim"), rel(paths.get("md", "")))
        if report.get("puzzles"):
            grid.add_row(Text("puzzles", style="dim"), ", ".join(report["puzzles"]))
        self.console.print()
        self.console.print(Panel(grid, border_style="green", title="review", title_align="left"))


def _check_detail(check: dict[str, Any]) -> str:
    if "with_rule" not in check:
        return f"  {check.get('error', '')}"
    w, c = check["with_rule"], check["control"]
    return f"  with the rule {w['blunders']} blunder(s), without it {c['blunders']}"
