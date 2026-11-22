"""Prompt text for the three model calls. The model only ever proposes moves or rule text."""

from __future__ import annotations

import json
from typing import Any

from sessionfish.moves import MAX_ALTERNATIVES, MAX_LINE_DEPTH, describe

ALTERNATIVES_SYSTEM = f"""You review a coding agent's session like a chess engine reviews a game.
You are shown the position before a move that an evaluator judged bad, the move that was
played, and why it scored badly. Propose up to {MAX_ALTERNATIVES} alternative lines the agent
could have played instead, each at most {MAX_LINE_DEPTH} moves, using only these tools:
Read(file_path), Edit(file_path, old_string, new_string, replace_all), Write(file_path, content)
and Bash(command). Paths are relative to the repo root. old_string must match the file exactly.
Only pytest and ruff commands are replayed. Prefer lines that fix the cause without editing
tests that already existed. Your lines are replayed and scored by code; you do not decide which
one is best."""

RULE_SYSTEM = """You turn a blunder in a coding agent's session into one rule for the project's
CLAUDE.md file. The rule must be one imperative sentence under 200 characters that would have
prevented the played move, without naming this specific file or test. A human reviews the rule,
then the position is replayed with the rule in context to check that it works."""

CONTINUE_SYSTEM = f"""You are a coding agent continuing a session from the position shown. Play
the next moves (at most {MAX_LINE_DEPTH}) with the tools Read, Edit, Write and Bash. Paths are
relative to the repo root and old_string must match the file exactly. Follow CLAUDE.md."""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n… (clipped)"


def history_lines(record: list[dict[str, Any]], upto: int) -> list[str]:
    out = []
    for r in record[1:upto]:
        move = r.get("move") or {}
        line = f"- ply {r['ply']}: {describe(move) if move else r.get('label', '?')} [{r['status']}]"
        if move.get("tool") == "Edit":
            line += f"\n  old: {_clip(move['old_string'], 200)!r}\n  new: {_clip(move['new_string'], 200)!r}"
        out.append(line)
    return out or ["- (no moves yet)"]


def position_block(task: str, record: list[dict[str, Any]], ply: int, files: dict[str, str]) -> str:
    before = record[ply - 1]
    parts = [
        f"Task given to the agent:\n{task}",
        "Moves so far:\n" + "\n".join(history_lines(record, ply)),
        f"Eval of this position: {before['score']:+d} (tests: {before['position']['tests']})",
    ]
    for path, text in files.items():
        parts.append(f"File {path} at this position:\n```\n{_clip(text, 4000)}\n```")
    return "\n\n".join(parts)


def worsened(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old = {c["name"]: c["value"] for c in before["position"]["components"]}
    out = []
    for c in after["position"]["components"]:
        delta = c["value"] - old.get(c["name"], 0)
        if delta < 0:
            out.append(f"{c['name']}: {delta:+d} ({'; '.join(map(str, c['items']))[:200]})")
    return out


def alternatives_prompt(task: str, record: list[dict[str, Any]], ply: int, files: dict[str, str]) -> str:
    played = record[ply]
    return "\n\n".join(
        [
            f"Critical ply: {ply} ({played['glyph']}, {played['severity']})",
            position_block(task, record, ply, files),
            "Played move:\n" + json.dumps(played.get("move"), indent=2),
            f"Eval after the played move: {played['score']:+d}. What got worse:\n"
            + "\n".join(worsened(record[ply - 1], played) or ["(token spend only)"]),
        ]
    )


def rule_prompt(task: str, record: list[dict[str, Any]], ply: int, files: dict[str, str], best: dict | None) -> str:
    parts = [alternatives_prompt(task, record, ply, files)]
    if best and best.get("moves"):
        parts.append("A better line found by replay:\n" + json.dumps(best["moves"], indent=2))
    return "\n\n".join(parts)


def continue_prompt(task: str, record: list[dict[str, Any]], ply: int, files: dict[str, str], rule: str) -> str:
    return "\n\n".join(
        [f"CLAUDE.md:\n{rule}" if rule else "CLAUDE.md: (empty)", position_block(task, record, ply, files)]
    )
