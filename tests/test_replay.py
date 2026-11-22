import pytest

from sessionfish.replay import ReplayError, apply_move, bash_allowed


def test_edit_write_read(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 1\n")
    assert (
        apply_move(tmp_path, {"tool": "Edit", "file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}).status
        == "applied"
    )
    assert (tmp_path / "a.py").read_text() == "x = 2\ny = 1\n"
    apply_move(tmp_path, {"tool": "Write", "file_path": "new/dir/b.py", "content": "z"})
    assert (tmp_path / "new/dir/b.py").read_text() == "z"
    assert apply_move(tmp_path, {"tool": "Read", "file_path": "a.py"}).status == "noop"


def test_edit_failures(tmp_path):
    (tmp_path / "a.py").write_text("v = 1\nv = 1\n")
    with pytest.raises(ReplayError, match="not found"):
        apply_move(tmp_path, {"tool": "Edit", "file_path": "a.py", "old_string": "w", "new_string": "q"})
    with pytest.raises(ReplayError, match="2 times"):
        apply_move(tmp_path, {"tool": "Edit", "file_path": "a.py", "old_string": "v = 1", "new_string": "v = 2"})
    with pytest.raises(ReplayError, match="does not exist"):
        apply_move(tmp_path, {"tool": "Edit", "file_path": "nope.py", "old_string": "a", "new_string": "b"})
    apply_move(
        tmp_path,
        {"tool": "Edit", "file_path": "a.py", "old_string": "v = 1", "new_string": "v = 2", "replace_all": True},
    )
    assert (tmp_path / "a.py").read_text() == "v = 2\nv = 2\n"


@pytest.mark.parametrize(
    ("cmd", "ok"),
    [
        ("pytest -q", True),
        ("python -m pytest tests/test_a.py", True),
        ("uv run ruff check .", True),
        ("PYTHONPATH=src pytest", True),
        (".venv/bin/pytest -x", True),
        ("rm -rf /", False),
        ("pytest && rm -rf build", False),
        ("pytest | tee out.txt", False),
        ("sed -i s/a/b/ file.py", False),
        ("echo $(pytest)", False),
    ],
)
def test_bash_allowlist(cmd, ok):
    assert bash_allowed(cmd) is ok


def test_bash_outside_allowlist_is_unreplayable(tmp_path):
    assert apply_move(tmp_path, {"tool": "Bash", "command": "make build"}).status == "unreplayable"
    assert apply_move(tmp_path, {"tool": "Bash", "command": "pytest"}).status == "noop"
