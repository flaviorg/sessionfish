"""Classify moves with code thresholds, never with the model.

A move is judged by how much the agent's winning chances fall, and whether they come back.
With a recovery window of W plies, the *sustained* drop of ply k is

    wc(k-1) - max(wc(k), ..., wc(k+W))

so a move that briefly breaks something and is fixed within W plies is not punished (writing a
failing test first, then the code, is a normal red-green cycle). The sustained drop is compared
with fixed thresholds: ``?!`` inaccuracy, ``?`` mistake, ``??`` blunder.

A *motif* names what went wrong, from the eval components that got worse. It is a label for
reports and for comparing sessions, not an input to the classification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sessionfish.evaluate import win_chance


@dataclass(frozen=True)
class Thresholds:
    inaccuracy: float = 0.10
    mistake: float = 0.20
    blunder: float = 0.30
    recovery_window: int = 2


GLYPHS = {"blunder": "??", "mistake": "?", "inaccuracy": "?!"}
MOTIFS = {
    "changed_asserts": "weakened-assertion",
    "protected_tests": "edited-existing-test",
    "regressed": "regression",
    "broken_suite": "broke-the-suite",
    "out_of_scope": "out-of-scope-edit",
    "lint": "lint-debt",
    "types": "type-debt",
}


def classify(drop: float, t: Thresholds) -> str | None:
    if drop >= t.blunder:
        return "blunder"
    if drop >= t.mistake:
        return "mistake"
    if drop >= t.inaccuracy:
        return "inaccuracy"
    return None


def motif(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """The component that lost the most value between two positions."""
    old = {c["name"]: c["value"] for c in before.get("components", [])}
    worst, worst_delta = None, 0
    for comp in after.get("components", []):
        delta = comp["value"] - old.get(comp["name"], 0)
        if delta < worst_delta and comp["name"] in MOTIFS:
            worst, worst_delta = comp["name"], delta
    return MOTIFS.get(worst) if worst else None


def annotate(record: list[dict[str, Any]], t: Thresholds | None = None) -> list[dict[str, Any]]:
    """Add ``wc``, ``drop``, ``severity``, ``glyph`` and ``motif`` to every ply of a game record.

    ``record[0]`` is the start position (ply 0); ``record[i]`` is the position after ply i and
    carries ``score`` (cp including the token penalty).
    """
    t = t or Thresholds()
    out = [dict(r) for r in record]
    for r in out:
        r["wc"] = round(win_chance(r["score"]), 4)
    for k in range(1, len(out)):
        prev = out[k - 1]["wc"]
        window = [out[j]["wc"] for j in range(k, min(len(out), k + t.recovery_window + 1))]
        sustained = prev - max(window)
        immediate = prev - out[k]["wc"]
        out[k]["drop"] = round(immediate, 4)
        out[k]["sustained_drop"] = round(sustained, 4)
        out[k]["recovered"] = immediate >= t.inaccuracy and sustained < t.inaccuracy
        severity = classify(sustained, t) if out[k].get("status") != "unreplayable" else None
        out[k]["severity"] = severity
        out[k]["glyph"] = GLYPHS.get(severity or "", "")
        out[k]["motif"] = motif(out[k - 1].get("position", {}), out[k].get("position", {})) if severity else None
    out[0].update(drop=0.0, sustained_drop=0.0, recovered=False, severity=None, glyph="", motif=None)
    return out


def critical_plies(annotated: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    """The ``limit`` annotated plies with the largest sustained drop, in game order."""
    marked = [r for r in annotated if r.get("severity")]
    top = sorted(marked, key=lambda r: (-r["sustained_drop"], r["ply"]))[:limit]
    return sorted(top, key=lambda r: r["ply"])


def summary(annotated: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"blunder": 0, "mistake": 0, "inaccuracy": 0, "unreplayable": 0}
    for r in annotated[1:]:
        if r.get("severity"):
            counts[r["severity"]] += 1
        if r.get("status") == "unreplayable":
            counts["unreplayable"] += 1
    return counts


def accuracy(annotated: list[dict[str, Any]]) -> float:
    """Lichess's per-move accuracy formula on the sustained drop in win percent, averaged."""
    scores = []
    for r in annotated[1:]:
        if r.get("status") == "unreplayable":
            continue
        loss = max(0.0, 50 * r["sustained_drop"])  # win chance [-1, 1] -> win percent points
        scores.append(min(100.0, max(0.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669)))
    return round(sum(scores) / len(scores), 1) if scores else 100.0
