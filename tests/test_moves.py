import pytest
from pydantic import ValidationError

from sessionfish.moves import Alternatives, Line, describe, parse_move


def test_paths_are_normalised_and_relative():
    assert parse_move({"tool": "Read", "file_path": "./src//a.py"}).file_path == "src/a.py"


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "a/../../b", ".git/config", "", "C:/x.py"])
def test_paths_that_escape_the_repo_are_rejected(path):
    with pytest.raises(ValidationError):
        parse_move({"tool": "Write", "file_path": path, "content": ""})


def test_unknown_tool_is_rejected():
    with pytest.raises(ValidationError):
        parse_move({"tool": "Delete", "file_path": "a.py"})


def test_lines_are_at_most_five_moves_and_three_alternatives():
    move = {"tool": "Bash", "command": "pytest"}
    Line(idea="ok", moves=[move] * 5)
    with pytest.raises(ValidationError):
        Line(idea="too deep", moves=[move] * 6)
    with pytest.raises(ValidationError):
        Line(idea="empty", moves=[])
    with pytest.raises(ValidationError):
        Alternatives(lines=[{"idea": "x", "moves": [move]}] * 4)


def test_describe():
    assert describe({"tool": "Edit", "file_path": "a.py"}) == "Edit a.py"
    assert describe({"tool": "Bash", "command": "pytest   -q"}) == "Bash pytest -q"
