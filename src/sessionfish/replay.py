"""Apply one move to a scratch checkout.

Edits and Writes are replayed byte for byte. Reads never change the position. Bash is never
executed here: an allowlisted command (pytest, ruff) only observes the position, which
``eval_position`` measures anyway, and anything else is marked unreplayable because its effect
on the files is unknown.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["applied", "noop", "unreplayable"]

ALLOWED_PROGRAMS = {"pytest", "ruff"}
_CHAINING = ("&&", "||", ";", "|", ">", "<", "`", "$(")


class ReplayError(RuntimeError):
    """The move cannot be applied to this position (for example old_string is missing)."""


@dataclass(frozen=True)
class ReplayResult:
    status: Status
    detail: str


def bash_allowed(command: str) -> bool:
    """True for a plain pytest or ruff invocation (optionally via python -m or uv run)."""
    if any(tok in command for tok in _CHAINING):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    while argv and "=" in argv[0] and not argv[0].startswith("-"):
        argv = argv[1:]  # leading VAR=value assignments
    if argv[:2] == ["uv", "run"]:
        argv = argv[2:]
    if len(argv) >= 3 and Path(argv[0]).name.startswith("python") and argv[1] == "-m":
        argv = argv[2:]
    return bool(argv) and Path(argv[0]).name in ALLOWED_PROGRAMS


def _target(workdir: Path, rel: str) -> Path:
    path = (workdir / rel).resolve()
    if not path.is_relative_to(workdir.resolve()):
        raise ReplayError(f"{rel} escapes the scratch checkout")
    return path


def apply_move(workdir: Path, move: dict) -> ReplayResult:
    tool = move["tool"]
    if tool == "Read":
        return ReplayResult("noop", "read only")
    if tool == "Bash":
        if bash_allowed(move["command"]):
            return ReplayResult("noop", "allowlisted command: the position is evaluated, not the command")
        return ReplayResult("unreplayable", "Bash outside the allowlist (pytest, ruff) is never run")
    path = _target(workdir, move["file_path"])
    if tool == "Write":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(move["content"], encoding="utf-8")
        return ReplayResult("applied", f"wrote {move['file_path']}")
    if tool == "Edit":
        if not path.is_file():
            raise ReplayError(f"{move['file_path']} does not exist")
        text = path.read_text(encoding="utf-8")
        old, new = move["old_string"], move["new_string"]
        count = text.count(old) if old else 0
        if count == 0:
            raise ReplayError(f"old_string not found in {move['file_path']}")
        if count > 1 and not move.get("replace_all"):
            raise ReplayError(f"old_string occurs {count} times in {move['file_path']} (replace_all is false)")
        path.write_text(text.replace(old, new) if move.get("replace_all") else text.replace(old, new, 1), "utf-8")
        return ReplayResult("applied", f"edited {move['file_path']}")
    raise ReplayError(f"unknown tool {tool!r}")
