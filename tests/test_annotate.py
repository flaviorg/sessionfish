from sessionfish.annotate import Thresholds, accuracy, annotate, classify, critical_plies, motif, summary


def rec(*scores, statuses=None):
    statuses = statuses or {}
    return [
        {"ply": i, "score": s, "status": statuses.get(i, "applied"), "position": {"components": []}}
        for i, s in enumerate(scores)
    ]


def test_thresholds():
    t = Thresholds()
    assert classify(0.05, t) is None
    assert classify(0.1, t) == "inaccuracy"
    assert classify(0.25, t) == "mistake"
    assert classify(0.5, t) == "blunder"


def test_sustained_drop_is_a_blunder():
    out = annotate(rec(0, 0, -300, -300, -300))
    assert out[2]["glyph"] == "??"
    assert [r["glyph"] for r in out if r["ply"] != 2] == ["", "", "", ""]


def test_a_dip_that_recovers_inside_the_window_is_not_punished():
    out = annotate(rec(0, -300, -300, 0, 0))
    assert out[1]["severity"] is None and out[1]["recovered"]
    out = annotate(rec(0, -300, -300, -300, 0))  # recovers one ply too late
    assert out[1]["severity"] == "blunder"


def test_window_zero_judges_every_move_on_its_own():
    out = annotate(rec(0, -300, 0), Thresholds(recovery_window=0))
    assert out[1]["severity"] == "blunder"


def test_unreplayable_plies_are_never_annotated():
    out = annotate(rec(0, -300, -300, -300, statuses={1: "unreplayable"}))
    assert out[1]["severity"] is None
    assert summary(out)["unreplayable"] == 1


def test_critical_plies_are_the_three_worst_in_game_order():
    out = annotate(rec(0, -60, 0, -300, 0, -700, 0, -200, 0), Thresholds(recovery_window=0))
    crit = critical_plies(out)
    assert len(crit) == 3
    assert [c["ply"] for c in crit] == [3, 5, 7]
    assert all(c["ply"] != 1 for c in crit)


def test_motif_is_the_component_that_lost_most():
    before = {"components": [{"name": "protected_tests", "value": 0}, {"name": "lint", "value": 0}]}
    after = {"components": [{"name": "protected_tests", "value": -200}, {"name": "lint", "value": -15}]}
    assert motif(before, after) == "edited-existing-test"


def test_accuracy():
    assert accuracy(annotate(rec(0, 0, 0))) == 100.0
    assert accuracy(annotate(rec(0, -2000, -2000))) < 60
