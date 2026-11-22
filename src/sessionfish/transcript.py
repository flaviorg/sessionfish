"""Parse a Claude Code session transcript (JSONL) into plies.

Claude Code writes one JSON object per line. Assistant turns carry ``tool_use`` blocks, user
turns carry the matching ``tool_result`` blocks, and one API message is often split across
several lines that repeat the same ``usage``, so token spend is counted once per message id.

Only ``Read``, ``Edit``, ``MultiEdit`` (expanded into Edits), ``Write`` and ``Bash`` become
plies. Other tools (Grep, Glob, TodoWrite, Task, ...) do not change the position and are
counted but skipped.
"""

from __future__ import annotations

import json
import posixpath
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sessionfish.moves import parse_move

MOVE_TOOLS = {"Read", "Edit", "MultiEdit", "Write", "Bash"}


class TranscriptError(ValueError):
    """The file is not a transcript sessionfish can read."""


@dataclass
class Ply:
    index: int  # 1-based ply number in the game
    tool: str
    raw_input: dict[str, Any]
    move: dict[str, Any] | None  # validated Move as a dict, or None when it cannot be replayed
    problem: str | None  # why `move` is None
    tool_use_id: str
    line_no: int  # 0-based index of the JSONL line that holds the tool_use
    tokens: int  # tokens of the assistant message that issued it (first ply of a message only)
    errored: bool = False  # the tool_result said is_error: the call had no effect in the session
    result_excerpt: str = ""


@dataclass
class Session:
    session_id: str
    cwd: str
    task: str
    plies: list[Ply]
    lines: list[str]  # raw JSONL lines, for transcript prefixes
    skipped_tools: Counter[str] = field(default_factory=Counter)

    @property
    def total_tokens(self) -> int:
        return sum(p.tokens for p in self.plies)


def _relativize(path: str, cwd: str) -> str:
    if not path.startswith("/"):
        return path
    cwd = cwd.rstrip("/")
    if cwd and (path == cwd or path.startswith(cwd + "/")):
        return posixpath.relpath(path, cwd)
    raise ValueError(f"{path} is outside the session cwd")


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text_of(c.get("text", c.get("content", ""))) for c in content if isinstance(c, dict))
    return ""


def _usage_tokens(usage: dict[str, Any] | None) -> int:
    if not usage:
        return 0
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)


def _expand(name: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    """MultiEdit -> one Edit per edit; everything else is one move."""
    if name == "MultiEdit":
        return [
            {"tool": "Edit", "file_path": raw.get("file_path", ""), **{k: e.get(k) for k in e}}
            for e in raw.get("edits", [])
        ]
    data = {"tool": name, **raw}
    if name == "Bash":
        data = {"tool": "Bash", "command": raw.get("command", "")}
    elif name == "Read":
        data = {"tool": "Read", "file_path": raw.get("file_path", "")}
    return [data]


def parse_session(path: str | Path) -> Session:
    text = Path(path).read_text(encoding="utf-8")
    return parse_lines(text.splitlines(), default_id=Path(path).stem)


def parse_lines(lines: list[str], default_id: str = "session") -> Session:
    entries: list[tuple[int, dict[str, Any]]] = []
    for no, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TranscriptError(f"line {no + 1} is not JSON: {exc}") from exc
        if isinstance(obj, dict):
            entries.append((no, obj))
    if not entries:
        raise TranscriptError("transcript is empty")

    session_id = next((o.get("sessionId") for _, o in entries if o.get("sessionId")), default_id)
    cwd = next((o.get("cwd") for _, o in entries if o.get("cwd")), "")
    task = ""
    results: dict[str, tuple[bool, str]] = {}
    for _, obj in entries:
        msg = obj.get("message") or {}
        if obj.get("type") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and not task:
            task = content.strip()
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    results[block.get("tool_use_id", "")] = (
                        bool(block.get("is_error")),
                        _text_of(block.get("content"))[:400],
                    )
                elif block.get("type") == "text" and not task:
                    task = str(block.get("text", "")).strip()

    usage_by_message: dict[str, int] = {}
    for no, obj in entries:
        msg = obj.get("message") or {}
        if obj.get("type") == "assistant" and msg.get("usage"):
            usage_by_message.setdefault(msg.get("id") or f"line-{no}", _usage_tokens(msg["usage"]))

    plies: list[Ply] = []
    skipped: Counter[str] = Counter()
    charged: set[str] = set()
    for no, obj in entries:
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        msg_id = msg.get("id") or f"line-{no}"
        for block in content:
            if block.get("type") != "tool_use":
                continue
            tokens = 0 if msg_id in charged else usage_by_message.get(msg_id, 0)
            name = block.get("name", "")
            if name not in MOVE_TOOLS:
                skipped[name] += 1
                continue
            raw = block.get("input") or {}
            errored, excerpt = results.get(block.get("id", ""), (False, ""))
            for data in _expand(name, raw):
                move, problem = None, None
                try:
                    if "file_path" in data:
                        data["file_path"] = _relativize(str(data["file_path"]), cwd)
                    move = parse_move(data).model_dump()
                except (ValueError, ValidationError) as exc:
                    problem = str(exc).splitlines()[0]
                plies.append(
                    Ply(
                        index=len(plies) + 1,
                        tool=data.get("tool", name),
                        raw_input=raw,
                        move=move,
                        problem=problem,
                        tool_use_id=block.get("id", ""),
                        line_no=no,
                        tokens=tokens,
                        errored=errored,
                        result_excerpt=excerpt,
                    )
                )
                if tokens:
                    charged.add(msg_id)
                tokens = 0
    return Session(session_id, cwd, task, plies, list(lines), skipped)


def prefix_before(session: Session, ply: Ply) -> list[str]:
    """Raw transcript lines strictly before the assistant line that played ``ply``."""
    return [line for line in session.lines[: ply.line_no] if line.strip()]
