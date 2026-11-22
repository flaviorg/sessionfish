import json

from sessionfish import scratch
from sessionfish.hook import find_baseline, record_baseline


def test_baseline_captures_head_and_every_uncommitted_change(fixture_repo, tmp_path):
    repo, sha = fixture_repo
    (repo / "shop" / "discount.py").write_text("changed = True\n")
    (repo / "notes.txt").write_text("untracked\n")
    path = record_baseline({"session_id": "abc", "cwd": str(repo), "transcript_path": "/x.jsonl", "source": "startup"})
    data = json.loads(path.read_text())
    assert data["sha"] == sha and path == find_baseline(repo, "abc")
    assert (repo / ".sessionfish" / ".gitignore").read_text() == "*\n"
    # the index was not touched
    assert scratch.git(repo, "diff", "--cached", "--name-only") == ""
    # sha + patch reproduces the working tree exactly
    clone = tmp_path / "clone"
    scratch.clone_scratch(repo, clone, sha)
    scratch.apply_patch(clone, data["patch"])
    assert (clone / "shop" / "discount.py").read_text() == "changed = True\n"
    assert (clone / "notes.txt").read_text() == "untracked\n"


def test_resumed_sessions_keep_their_original_baseline(fixture_repo):
    repo, _ = fixture_repo
    record_baseline({"session_id": "s", "cwd": str(repo), "source": "startup"})
    (repo / "later.txt").write_text("x\n")
    path = record_baseline({"session_id": "s", "cwd": str(repo), "source": "resume"})
    assert "later.txt" not in json.loads(path.read_text())["patch"]
