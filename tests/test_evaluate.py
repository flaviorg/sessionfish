from sessionfish.evaluate import EvalSettings, Weights, parse_junit, score, win_chance

BASE = {"tests": {"t::a": "passed", "t::b": "failed"}, "broken": False, "lint": 2, "types": None}
W = Weights()


def run(tests, *, changed=None, protected=None, asserts=None, lint=2, scope=(), broken=False):
    facts = {"tests": tests, "broken": broken, "lint": lint, "types": None}
    return score(
        facts,
        BASE,
        changed=changed or {},
        protected=protected or {},
        current_asserts=asserts or {},
        settings=EvalSettings(scope=scope),
    )


def comp(result, name):
    return next(c for c in result["components"] if c["name"] == name)


def test_base_scores_zero():
    assert run(BASE["tests"])["cp"] == 0


def test_fixed_and_regressed_tests():
    r = run({"t::a": "failed", "t::b": "passed"})
    assert comp(r, "fixed")["value"] == W.fixed
    assert comp(r, "regressed")["value"] == W.regressed
    assert r["cp"] == W.fixed + W.regressed


def test_new_failing_tests_are_expected_red():
    r = run({**BASE["tests"], "t::new_red": "failed", "t::new_green": "passed"})
    assert comp(r, "expected_red")["items"] == ["t::new_red"]
    assert comp(r, "expected_red")["value"] == 0
    assert r["cp"] == W.new_green


def test_deleted_existing_test_counts_as_regression():
    assert comp(run({"t::b": "failed"}), "regressed")["items"] == ["t::a"]


def test_protected_test_edits_and_changed_asserts():
    protected = {"tests/test_x.py": ["assert f(10) == 90", "assert g() == 1"]}
    r = run(
        BASE["tests"],
        changed={"tests/test_x.py": 2},
        protected=protected,
        asserts={"tests/test_x.py": ["assert f(10) <= 100", "assert g() == 1"]},
    )
    assert comp(r, "protected_tests")["value"] == W.protected_file
    assert comp(r, "changed_asserts")["items"] == ["tests/test_x.py: assert f(10) == 90"]


def test_out_of_scope_lines_are_capped_and_lint_is_a_delta():
    r = run(BASE["tests"], changed={"src/a.py": 3, "README.md": 1000}, scope=("src/**",), lint=5)
    assert comp(r, "out_of_scope")["value"] == W.out_of_scope_cap
    assert comp(r, "lint")["value"] == 3 * W.lint


def test_broken_suite():
    assert comp(run({}, broken=True), "broken_suite")["value"] == W.broken_suite


def test_win_chance_is_monotone_and_bounded():
    values = [win_chance(cp) for cp in (-2000, -300, 0, 300, 2000)]
    assert values == sorted(values)
    assert -1 < values[0] < 0 == values[2] < values[-1] < 1


def test_parse_junit():
    xml = """<testsuites><testsuite>
      <testcase classname="tests.test_a" name="test_ok"/>
      <testcase classname="tests.test_a" name="test_bad"><failure message="x"/></testcase>
      <testcase classname="tests.test_a" name="test_skip"><skipped/></testcase>
      <testcase classname="" name="tests.test_b"><error message="collection"/></testcase>
    </testsuite></testsuites>"""
    tests, errors = parse_junit(xml)
    assert tests == {
        "tests.test_a::test_ok": "passed",
        "tests.test_a::test_bad": "failed",
        "tests.test_a::test_skip": "skipped",
    }
    assert errors == 1


def test_settings_digest_is_stable():
    assert EvalSettings(scope=("a",)).digest() == EvalSettings(scope=("a",)).digest()
    assert EvalSettings(scope=("a",)).digest() != EvalSettings(scope=("b",)).digest()
