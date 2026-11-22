"""A deterministic, scripted stand-in for a model.

Rules match on the prompt text, never on call order, so the fake is stateless: concurrent
alternative lines, resumed interrupts and repeated runs all get the same answers. This is what
makes the demo and the end-to-end tests reproducible offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from sessionfish.llm.port import LLMReply, T
from sessionfish.moves import Alternatives, Line, RuleProposal

SCHEMA_KEYS: dict[type[BaseModel], str] = {Alternatives: "alternatives", RuleProposal: "rule", Line: "line"}


@dataclass(frozen=True)
class Rule:
    schema: str  # "alternatives" | "rule" | "line"
    respond: dict[str, Any]
    when: tuple[str, ...] = ()
    unless: tuple[str, ...] = ()

    def matches(self, schema: str, prompt: str) -> bool:
        return (
            schema == self.schema and all(s in prompt for s in self.when) and not any(s in prompt for s in self.unless)
        )


FALLBACKS: dict[str, dict[str, Any]] = {
    "alternatives": {"lines": []},
    "rule": {"rule": "Prefer the smallest change that keeps every existing test meaningful.", "rationale": "fallback"},
    "line": {"idea": "re-run the tests", "moves": [{"tool": "Bash", "command": "pytest -q"}]},
}


@dataclass
class ScriptedLLM:
    rules: list[Rule] = field(default_factory=list)
    name: str = "fake"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def structured(self, system: str, user: str, schema: type[T]) -> LLMReply[T]:
        key = SCHEMA_KEYS.get(schema)
        if key is None:
            raise TypeError(f"ScriptedLLM has no answers for {schema.__name__}")
        self.calls.append((key, user))
        rule = next((r for r in self.rules if r.matches(key, user)), None)
        value = schema.model_validate(rule.respond if rule else FALLBACKS[key])
        tokens = (len(system) + len(user) + len(json.dumps(value.model_dump()))) // 4
        return LLMReply(value, tokens)
