import json

from typer.testing import CliRunner

from sessionfish import __version__
from sessionfish.cli import app
from tests.conftest import DEMO_JSONL

runner = CliRunner()


def test_version_graph_and_hook_install():
    assert runner.invoke(app, ["version"]).stdout.strip() == __version__
    graph = runner.invoke(app, ["graph"]).stdout
    assert "eval_position" in graph and "play_line" in graph
    snippet = runner.invoke(app, ["hook", "install"]).stdout
    assert "sessionfish hook session-start" in snippet


def test_hook_never_fails_the_session(tmp_path):
    result = runner.invoke(app, ["hook", "session-start"], input=json.dumps({"cwd": str(tmp_path), "session_id": "x"}))
    assert result.exit_code == 0  # not a git repo: reported on stderr, exit 0


def test_review_code_only_with_fail_on(fixture_repo, tmp_path):
    repo, sha = fixture_repo
    args = [
        "review",
        str(DEMO_JSONL),
        "--repo",
        str(repo),
        "--base",
        sha,
        "--out",
        str(tmp_path / "o"),
        "--no-analysis",
        "--scope",
        "shop/**",
        "--scope",
        "tests/**",
        "--json",
    ]
    result = runner.invoke(app, [*args, "--fail-on", "blunder"])
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["summary"]["blunder"] == 1 and report["analysis"] == []
    assert runner.invoke(app, args).exit_code == 0


def test_review_reports_bad_transcripts(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    result = runner.invoke(app, ["review", str(bad), "--repo", str(tmp_path)])
    assert result.exit_code == 2
