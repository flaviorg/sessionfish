"""Scratch checkouts and git tree hashes.

Every replay happens in a ``git clone --shared`` of the reviewed repo, so sessionfish never
writes to that repo's ``.git``: objects it creates (trees for every position) live in the
scratch clone. A position is identified by the hash of its git tree, which is also the cache
key for evaluation and what a puzzle stores.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "sessionfish",
    "GIT_AUTHOR_EMAIL": "sessionfish@localhost",
    "GIT_COMMITTER_NAME": "sessionfish",
    "GIT_COMMITTER_EMAIL": "sessionfish@localhost",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


# tool caches never become part of a position
SCRATCH_IGNORES = ("__pycache__/", "*.pyc", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/", ".coverage")


class GitError(RuntimeError):
    pass


def git(cwd: str | Path, *args: str, input: str | bytes | None = None, check: bool = True) -> str:
    env = {**os.environ, **GIT_ENV}
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    data = input.encode() if isinstance(input, str) else input
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        input=data,
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode(errors="replace").strip()


def init_fixture_repo(path: Path) -> str:
    """Turn a plain directory into a repo with one deterministic commit; returns its sha."""
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "fixture base")
    return git(path, "rev-parse", "HEAD")


def resolve_sha(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")


def clone_scratch(source: Path, dest: Path, sha: str) -> None:
    """A shared clone of ``source`` (or of another scratch clone) checked out at ``sha``."""
    source, dest = source.resolve(), dest.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(dest.parent, "clone", "-q", "--shared", "--no-checkout", str(source), str(dest))
    git(dest, "checkout", "-q", "--detach", sha)
    exclude = dest / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("\n".join(SCRATCH_IGNORES) + "\n")


def apply_patch(workdir: Path, patch: str) -> None:
    if patch.strip():
        git(workdir, "apply", "--whitespace=nowarn", "-", input=patch if patch.endswith("\n") else patch + "\n")


def write_tree(workdir: Path) -> str:
    """Hash of the working tree as it is now (untracked files included, .gitignore respected)."""
    git(workdir, "add", "-A")
    return git(workdir, "write-tree")


def restore_tree(workdir: Path, tree: str) -> None:
    """Make the working directory match ``tree`` exactly."""
    git(workdir, "read-tree", "--reset", "-u", tree)
    git(workdir, "clean", "-fdq")


def changed_files(workdir: Path, base_tree: str, tree: str) -> list[str]:
    out = git(workdir, "diff", "--name-only", "--no-renames", base_tree, tree)
    return [line for line in out.splitlines() if line]


def numstat(workdir: Path, base_tree: str, tree: str) -> dict[str, int]:
    """Changed lines (added + deleted) per file between two trees. Binary files count as 1."""
    out = git(workdir, "diff", "--numstat", "--no-renames", base_tree, tree)
    stats: dict[str, int] = {}
    for line in out.splitlines():
        added, deleted, name = line.split("\t", 2)
        stats[name] = 1 if added == "-" else int(added) + int(deleted)
    return stats


def show(workdir: Path, tree: str, path: str) -> str | None:
    out = subprocess.run(["git", "show", f"{tree}:{path}"], cwd=workdir, capture_output=True, timeout=60, check=False)
    return out.stdout.decode(errors="replace") if out.returncode == 0 else None


def diff(workdir: Path, old_tree: str, new_tree: str, max_chars: int = 6000) -> str:
    out = git(workdir, "diff", "--no-renames", old_tree, new_tree)
    return out if len(out) <= max_chars else out[:max_chars] + "\n… (diff clipped)"


def list_files(workdir: Path, tree: str) -> list[str]:
    return git(workdir, "ls-tree", "-r", "--name-only", tree).splitlines()
