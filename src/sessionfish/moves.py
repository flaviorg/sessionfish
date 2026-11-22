"""The move vocabulary: the four tool calls sessionfish understands.

Moves parsed from a transcript and moves proposed by a model share these schemas, so an
alternative line is validated exactly like a recorded one. Paths are always repo-relative.
"""

from __future__ import annotations

import posixpath
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator

MAX_LINE_DEPTH = 5
MAX_ALTERNATIVES = 3


def _clean_path(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if not value or value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        raise ValueError(f"path must be relative to the repo root: {value!r}")
    norm = posixpath.normpath(value)
    if norm == ".." or norm.startswith("../") or norm == ".":
        raise ValueError(f"path escapes the repo: {value!r}")
    if norm == ".git" or norm.startswith(".git/"):
        raise ValueError(f"path is inside .git: {value!r}")
    return norm


class _FileMove(BaseModel):
    file_path: str = Field(description="Repo-relative path, for example src/app/module.py.")

    @field_validator("file_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _clean_path(value)


class ReadMove(_FileMove):
    tool: Literal["Read"] = "Read"


class EditMove(_FileMove):
    tool: Literal["Edit"] = "Edit"
    old_string: str = Field(description="Exact text to replace. Must occur exactly once unless replace_all.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = False


class WriteMove(_FileMove):
    tool: Literal["Write"] = "Write"
    content: str = Field(description="Full new file content.")


class BashMove(BaseModel):
    tool: Literal["Bash"] = "Bash"
    command: str = Field(description="Shell command. Only pytest and ruff are replayed.")


Move = Annotated[ReadMove | EditMove | WriteMove | BashMove, Field(discriminator="tool")]
MOVE_ADAPTER: TypeAdapter[Move] = TypeAdapter(Move)


def parse_move(data: dict) -> Move:
    return MOVE_ADAPTER.validate_python(data)


def describe(move: dict) -> str:
    """One short human label for a move, for the move list and prompts."""
    tool = move.get("tool")
    if tool == "Bash":
        cmd = " ".join(str(move.get("command", "")).split())
        return f"Bash {cmd[:60]}" + ("…" if len(cmd) > 60 else "")
    return f"{tool} {move.get('file_path', '?')}"


class Line(BaseModel):
    """An alternative continuation from a position: at most five moves."""

    idea: str = Field(description="One sentence: what this line tries instead of the played move.")
    moves: list[Move] = Field(min_length=1, max_length=MAX_LINE_DEPTH)


class Alternatives(BaseModel):
    """Up to three candidate lines from the position before a critical move."""

    lines: list[Line] = Field(default_factory=list, max_length=MAX_ALTERNATIVES)


class RuleProposal(BaseModel):
    """A CLAUDE.md rule that, had it been in context, should have prevented the blunder."""

    rule: str = Field(description="One imperative sentence for CLAUDE.md, under 200 characters.", max_length=300)
    rationale: str = Field(description="Which move it would have prevented and why.")


class RuleDecision(BaseModel):
    """The human's answer to a proposed rule (the interrupt response schema)."""

    accept: bool
    rule: str | None = Field(default=None, description="Edited rule text; omit to keep the proposal.")
    note: str | None = None
