import json
from pathlib import Path


def test_demo_review_finds_the_weakened_assertion(demo_report):
    r = demo_report
    assert r["summary"] == {"blunder": 1, "mistake": 0, "inaccuracy": 1, "unreplayable": 1}
    by_ply = {p["ply"]: p for p in r["record"]}
    assert by_ply[6]["glyph"] == "??"
    assert by_ply[6]["motif"] == "edited-existing-test"
    assert by_ply[4]["glyph"] == "?!" and by_ply[4]["motif"] == "out-of-scope-edit"
    # writing a failing test first is expected red, not a mistake
    assert by_ply[3]["glyph"] == "" and by_ply[3]["score"] > 0
    assert by_ply[5]["status"] == "unreplayable"


def test_analysis_forks_three_lines_and_code_picks_the_best(demo_report):
    a6 = next(a for a in demo_report["analysis"] if a["ply"] == 6)
    statuses = [(x["n"], x["status"]) for x in a6["results"]]
    assert statuses == [(1, "ok"), (2, "ok"), (3, "failed")]
    assert "old_string not found" in a6["results"][2]["error"]  # routed to the error handler
    assert a6["best"]["n"] == 1 and a6["best"]["better"]
    assert a6["results"][0]["final_score"] > 0 > a6["results"][1]["final_score"]
    assert a6["results"][1]["blunders"] == 1  # editing the test's input is still a blunder


def test_blunder_becomes_a_puzzle_and_the_rule_is_verified(demo_report):
    a6 = next(a for a in demo_report["analysis"] if a["ply"] == 6)
    assert a6["puzzle"]["id"].endswith("-ply6")
    assert a6["decision"]["accept"] is True
    check = a6["rule_check"]
    assert check["verdict"] == "verified"
    assert check["with_rule"]["blunders"] == 0 and check["control"]["blunders"] == 1
    kinds = [k for k, _ in demo_report["_events"]]
    assert kinds.index("rule_proposed") < kinds.index("rule_decided")


def test_inaccuracies_get_lines_but_no_puzzle(demo_report):
    a4 = next(a for a in demo_report["analysis"] if a["ply"] == 4)
    assert a4["best"]["n"] == 1 and a4["puzzle"] is None and a4["rule"] is None


def test_outputs_are_written_and_redacted(demo_report):
    out = Path(demo_report["paths"]["dir"])
    for name in ("report.json", "report.md", "index.html", "puzzles"):
        assert (out / name).exists()
    assert not (out / "scratch").exists()
    data = json.loads((out / "report.json").read_text())
    assert data["session"]["cwd"] == "/home/<user>/discount_shop"
    assert "??" in (out / "report.md").read_text()
    puzzle = json.loads(next((out / "puzzles").glob("*-ply6.json")).read_text())
    assert puzzle["tree_before"] == next(p for p in data["record"] if p["ply"] == 5)["tree"]
    assert "/home/dev" not in (out / "puzzles" / puzzle["prefix"]).read_text()


def test_puzzle_reproduces_from_sha_and_prefix(demo_report, tmp_path):
    from sessionfish.annotate import Thresholds
    from sessionfish.evaluate import EvalSettings
    from sessionfish.llm.demo_script import RULE_TEXT, demo_llm
    from sessionfish.review import verify_puzzle
    from tests.conftest import DEMO_SCOPE

    out = Path(demo_report["paths"]["dir"])
    puzzle = next((out / "puzzles").glob("*-ply6.json"))
    result = verify_puzzle(
        puzzle,
        Path(demo_report["_repo"]),
        RULE_TEXT,
        demo_llm(),
        tmp_path / "pw",
        EvalSettings(scope=DEMO_SCOPE),
        Thresholds(),
    )
    assert result["reproduced"] and result["eval_before"] == result["expected_eval"]
    assert result["verdict"] == "verified"
    weak = verify_puzzle(
        puzzle,
        Path(demo_report["_repo"]),
        "Keep answers short.",
        demo_llm(),
        tmp_path / "pw2",
        EvalSettings(scope=DEMO_SCOPE),
        Thresholds(),
    )
    assert weak["verdict"] == "failed"  # an unrelated rule does not prevent the blunder
