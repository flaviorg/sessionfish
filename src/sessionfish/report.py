"""Markdown report: the game record with annotations, then the analysis of each critical ply."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _eval(score: int) -> str:
    return f"{score / 100:+.2f}"


def _moves(moves: list[dict[str, Any]]) -> str:
    from sessionfish.moves import describe

    return " → ".join(f"`{describe(m)}`" for m in moves)


def render_markdown(report: dict[str, Any]) -> str:
    s = report["session"]
    summ = report["summary"]
    out = [
        f"# sessionfish review: `{s['id']}`",
        "",
        f"> {s['task']}",
        "",
        f"- **Plies:** {s['plies']} ({report['positions']['distinct']} distinct positions, the rest were cache hits)",
        f"- **Annotations:** {summ['blunder']} blunder(s) ??, {summ['mistake']} mistake(s) ?, "
        f"{summ['inaccuracy']} inaccurac(y/ies) ?!, {summ['unreplayable']} unreplayable",
        f"- **Accuracy:** {report['accuracy']}%",
        f"- **Base:** `{report['base']['sha'][:12]}`" + (" + dirty patch" if report["base"]["dirty"] else ""),
        f"- **Scope:** {', '.join(f'`{g}`' for g in report['scope']) or 'everything'}",
        f"- **Analysis model:** `{report['model']}`",
        "",
        "## Game record",
        "",
        "| Ply | Move | Replay | Tests | Eval | |",
        "| ---: | --- | --- | --- | ---: | --- |",
    ]
    for r in report["record"]:
        t = r["position"]["tests"]
        glyph = r["glyph"] + (f" {r['motif']}" if r.get("motif") else "")
        out.append(
            f"| {r['ply']} | `{r['label']}` | {r['status']} | {t['passed']}/{t['total']} "
            f"| {_eval(r['score'])} | {glyph} |"
        )
    for a in report["analysis"]:
        played = next(r for r in report["record"] if r["ply"] == a["ply"])
        out += ["", f"## Ply {a['ply']} {a['glyph']}: `{played['label']}`", ""]
        if a.get("proposal_error"):
            out.append(f"Model error: {a['proposal_error']}")
        for res in a["results"]:
            if res["status"] == "ok":
                out.append(f"- Line {res['n']} ({_eval(res['final_score'])}): {res['idea']} {_moves(res['moves'])}")
            else:
                out.append(f"- Line {res['n']} **{res['status']}**: {res['idea']} ({res.get('error', '')})")
        best = a.get("best")
        if best:
            verdict = "better than the game" if best["better"] else "no clear improvement"
            out.append(f"\n**Best line:** line {best['n']}, {verdict} (win-chance gain {best['gain']:+.2f}).")
        if a.get("puzzle"):
            out.append(
                f"\n**Puzzle:** `{a['puzzle']['id']}` (base `{a['puzzle']['base_sha'][:12]}` + prefix, eval "
                f"{_eval(a['puzzle']['eval_before']['score'])})."
            )
        if a.get("rule"):
            decision = a.get("decision") or {}
            state = "accepted" if decision.get("accept") else "not accepted"
            out.append(f"\n**Proposed CLAUDE.md rule** ({state}): {decision.get('rule') or a['rule']['rule']}")
            check = a.get("rule_check")
            if check:
                out.append(f"\n**Rule check:** {check['verdict']}.")
                for key in ("with_rule", "control"):
                    if key in check:
                        c = check[key]
                        label = "with the rule" if key == "with_rule" else "without it"
                        out.append(f"- {label}: {_moves(c['moves'])}, {c['blunders']} blunder(s)")
    out.append("")
    return "\n".join(out)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    path.write_text(render_markdown(report), encoding="utf-8")
