"""The SessionStart hook: record where a session started, so it can be replayed later.

Claude Code runs the hook with a JSON payload on stdin (``session_id``, ``transcript_path``,
``cwd``, ...). We store the HEAD sha plus a patch of every uncommitted change (tracked diffs
and untracked files), which together are the exact base position the agent started from. The
hook never blocks or fails a session: any problem is printed to stderr and it exits 0.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sessionfish import scratch

MAX_UNTRACKED_FILES = 200
MAX_UNTRACKED_BYTES = 1_000_000

SETTINGS_SNIPPET = {
    "hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "sessionfish hook session-start"}]}],
    }
}


def baseline_dir(repo: Path) -> Path:
    return repo / ".sessionfish" / "baselines"


def dirty_patch(repo: Path) -> str:
    """Tracked changes against HEAD plus untracked files as new-file diffs, without touching the index."""
    parts = [scratch.git(repo, "diff", "--binary", "HEAD", check=False)]
    untracked = scratch.git(repo, "ls-files", "--others", "--exclude-standard", "-z", check=False).split("\0")
    for name in [u for u in untracked if u][:MAX_UNTRACKED_FILES]:
        path = repo / name
        if not path.is_file() or path.stat().st_size > MAX_UNTRACKED_BYTES or name.startswith(".sessionfish/"):
            continue
        proc = subprocess.run(
            ["git", "diff", "--binary", "--no-index", "--", "/dev/null", name],
            cwd=repo,
            capture_output=True,
            timeout=60,
            check=False,
        )
        parts.append(proc.stdout.decode(errors="replace"))
    return "\n".join(p.rstrip("\n") for p in parts if p.strip()) + ("\n" if any(p.strip() for p in parts) else "")


def record_baseline(payload: dict[str, Any]) -> Path:
    cwd = Path(payload.get("cwd") or ".").resolve()
    repo = Path(scratch.git(cwd, "rev-parse", "--show-toplevel"))
    sha = scratch.git(repo, "rev-parse", "--verify", "HEAD")
    session_id = str(payload.get("session_id") or "unknown")
    out_dir = baseline_dir(repo)
    out_dir.mkdir(parents=True, exist_ok=True)
    gitignore = repo / ".sessionfish" / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")  # never commit baselines or reviews
    record = {
        "session_id": session_id,
        "transcript_path": payload.get("transcript_path"),
        "source": payload.get("source"),
        "cwd": str(cwd),
        "sha": sha,
        "patch": dirty_patch(repo),
    }
    path = out_dir / f"{session_id}.json"
    if path.exists() and payload.get("source") in ("resume", "compact"):
        return path  # a resumed session keeps the position it originally started from
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def find_baseline(repo: Path, session_id: str) -> Path | None:
    path = baseline_dir(repo) / f"{session_id}.json"
    return path if path.exists() else None
