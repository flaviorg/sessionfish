import json

import pytest

from sessionfish.transcript import TranscriptError, parse_lines, parse_session, prefix_before
from tests.conftest import DEMO_JSONL


def _assistant(msg_id, blocks, usage=None, cwd="/w"):
    return json.dumps(
        {
            "type": "assistant",
            "cwd": cwd,
            "sessionId": "s1",
            "message": {"id": msg_id, "content": blocks, "usage": usage},
        }
    )


def _result(tool_id, text="ok", is_error=False):
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": is_error}]
            },
        }
    )


def test_demo_transcript_parses_into_nine_plies():
    s = parse_session(DEMO_JSONL)
    assert s.session_id.startswith("5e55f150")
    assert [p.tool for p in s.plies] == ["Read", "Bash", "Write", "Edit", "Bash", "Edit", "Bash", "Edit", "Bash"]
    assert all(p.move is not None for p in s.plies)
    assert s.plies[0].move["file_path"] == "shop/discount.py"
    assert "failing" in s.task


def test_usage_repeated_on_split_lines_is_counted_once():
    usage = {"input_tokens": 100, "output_tokens": 20}
    lines = [
        _assistant("m1", [{"type": "text", "text": "hi"}], usage),
        _assistant("m1", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/w/a.py"}}], usage),
        _assistant("m1", [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/w/b.py"}}], usage),
    ]
    s = parse_lines(lines)
    assert [p.tokens for p in s.plies] == [120, 0]
    assert s.total_tokens == 120


def test_multiedit_expands_and_other_tools_are_skipped():
    edit = {
        "file_path": "/w/a.py",
        "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}],
    }
    lines = [
        _assistant("m1", [{"type": "tool_use", "id": "t1", "name": "MultiEdit", "input": edit}]),
        _assistant("m2", [{"type": "tool_use", "id": "t2", "name": "Grep", "input": {"pattern": "x"}}]),
    ]
    s = parse_lines(lines)
    assert [(p.tool, p.move["old_string"]) for p in s.plies] == [("Edit", "a"), ("Edit", "c")]
    assert s.skipped_tools["Grep"] == 1


def test_paths_outside_the_session_cwd_are_unreplayable():
    s = parse_lines(
        [_assistant("m1", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/etc/hosts"}}])]
    )
    assert s.plies[0].move is None and "outside" in s.plies[0].problem


def test_failed_tool_calls_are_marked():
    lines = [
        _assistant(
            "m1",
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Edit",
                    "input": {"file_path": "/w/a.py", "old_string": "x", "new_string": "y"},
                }
            ],
        ),
        _result("t1", "String to replace not found", is_error=True),
    ]
    assert parse_lines(lines).plies[0].errored


def test_bad_input_raises():
    with pytest.raises(TranscriptError):
        parse_lines(["not json"])
    with pytest.raises(TranscriptError):
        parse_lines([""])


def test_prefix_stops_before_the_ply():
    s = parse_session(DEMO_JSONL)
    prefix = prefix_before(s, s.plies[5])
    assert all("toolu_0006" not in line for line in prefix)
    assert any("toolu_0005" in line for line in prefix)
