from sessionfish import evaluate, scratch
from sessionfish.evaluate import EvalSettings
from sessionfish.game import fork, line_from_fork, main_line
from sessionfish.review import Baseline, play_game
from sessionfish.transcript import parse_session
from tests.conftest import DEMO_JSONL, DEMO_SCOPE


def test_checkpoint_history_is_the_game_record_and_evals_are_cached(fixture_repo, tmp_path, monkeypatch):
    repo, sha = fixture_repo
    calls = []
    real = evaluate.measure
    monkeypatch.setattr(evaluate, "measure", lambda wd, s: calls.append(1) or real(wd, s))
    session = parse_session(DEMO_JSONL)
    app, thread, record = play_game(session, repo, Baseline(sha), tmp_path / "w", EvalSettings(scope=DEMO_SCOPE), "t")

    assert [r["ply"] for r in record] == list(range(10))
    # every position comes from a checkpoint: the record is read back from get_state_history()
    ids = {s.config["configurable"]["checkpoint_id"] for s in app.get_state_history(thread)}
    assert all(r["checkpoint"]["configurable"]["checkpoint_id"] in ids for r in record)
    # Reads, test runs and the unreplayable grep never change the tree, so they are cache hits:
    # one measurement for the base (prepare) plus one per distinct new tree
    distinct = len({r["tree"] for r in record})
    assert distinct == 5
    assert len(calls) == distinct
    assert [r["status"] for r in record][1:] == [
        "noop",
        "noop",
        "applied",
        "applied",
        "unreplayable",
        "applied",
        "noop",
        "applied",
        "noop",
    ]
    assert record[6]["position"]["tests"] == {"passed": 6, "failed": 1, "total": 7}
    # the reviewed repo is never written to: no new objects, no worktrees, clean status
    assert scratch.git(repo, "status", "--porcelain") == ""
    assert scratch.git(repo, "worktree", "list").count("\n") == 0


def test_fork_replays_a_line_from_any_position(fixture_repo, tmp_path):
    repo, sha = fixture_repo
    settings = EvalSettings(scope=DEMO_SCOPE)
    app, thread, record = play_game(parse_session(DEMO_JSONL), repo, Baseline(sha), tmp_path / "w", settings, "t")
    fix = {"tool": "Edit", "file_path": "shop/discount.py", "old_string": "> BULK", "new_string": ">= BULK"}
    cfg = fork(
        app, record[2]["checkpoint"], [{"ply": 3, "move": fix, "label": "fix"}], workdir=tmp_path / "f", line="alt"
    )
    app.invoke(None, cfg)
    line = line_from_fork(app, thread, cfg, settings.weights)
    assert [r["ply"] for r in line] == [2, 3]
    assert line[0]["tree"] == record[2]["tree"]
    assert line[1]["position"]["tests"] == {"passed": 5, "failed": 0, "total": 5}
    # the main line is untouched by the fork: reading it from its own leaf gives the same game
    again = main_line(app, thread, settings.weights, leaf_cfg=record[-1]["checkpoint"])
    assert [r["tree"] for r in again] == [r["tree"] for r in record]
    assert app.get_state(thread).values["line"] == "alt"  # the newest checkpoint is the fork's


def test_dirty_patch_is_part_of_the_base(fixture_repo, tmp_path):
    repo, sha = fixture_repo
    patch = scratch.git(repo, "diff")  # empty
    (repo / "shop" / "discount.py").write_text((repo / "shop" / "discount.py").read_text().replace("> BULK", ">= BULK"))
    patch = scratch.git(repo, "diff") + "\n"
    session = parse_session(DEMO_JSONL)
    session.plies = session.plies[:1]
    _, _, record = play_game(session, repo, Baseline(sha, patch), tmp_path / "w", EvalSettings(), "t")
    assert record[0]["position"]["tests"]["failed"] == 0
