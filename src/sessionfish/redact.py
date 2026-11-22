"""Scrub secrets and home-directory paths from anything sessionfish writes out.

Transcripts carry whatever the agent saw: env dumps, config files, tokens pasted into a
prompt. Reports, the HTML viewer and puzzle prefixes all go through ``redact`` first. This is
a best-effort pattern list, not a guarantee; review a report before you share it.
"""

from __future__ import annotations

import re

MASK = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), MASK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"), MASK),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), MASK),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"), MASK),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), MASK),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/\-]{16,}=*"), r"\1 " + MASK),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIALS?)[A-Z0-9_]*)"
            r"(\s*[=:]\s*)([\"']?)([^\s\"'\\()\[\]{},;]{6,})\3(?=[\s,;)}\]]|$)"
        ),
        r"\1\2\3" + MASK + r"\3",
    ),
    (re.compile(r"(/Users/|/home/)[A-Za-z0-9._\-]+"), r"\1<user>"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_obj(value: object) -> object:
    """Redact every string inside a JSON-like structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    return value
