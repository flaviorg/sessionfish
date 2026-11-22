"""Scripted answers for the demo session (examples/sessions/weakened-assert.jsonl).

At ply 6 the agent weakened an assertion. The scripted analysis proposes three lines from the
position before it: the real fix (best), editing the test's input instead (still bad), and a
line whose first Edit does not apply (it fails, and the error handler records it). Then it
proposes a CLAUDE.md rule, and when that rule is in the prompt the continuation is the fix.
"""

from __future__ import annotations

from sessionfish.llm.fake import Rule, ScriptedLLM

FIX_THRESHOLD = {
    "tool": "Edit",
    "file_path": "shop/discount.py",
    "old_string": "if quantity > BULK_THRESHOLD:",
    "new_string": "if quantity >= BULK_THRESHOLD:",
}
RUN_TESTS = {"tool": "Bash", "command": "pytest -q"}
WEAKEN = {
    "tool": "Edit",
    "file_path": "tests/test_discount.py",
    "old_string": 'assert order_total(Decimal("10.00"), 10) == Decimal("90.00")',
    "new_string": 'assert order_total(Decimal("10.00"), 10) <= Decimal("100.00")',
}
RULE_TEXT = (
    "Never edit an existing test's assertion or inputs to make it pass; fix the code under test, "
    "and ask before changing a test you believe is wrong."
)

DEMO_RULES = [
    Rule(
        "alternatives",
        when=("Critical ply: 6",),
        respond={
            "lines": [
                {
                    "idea": "Fix the off-by-one in the code: ten items should already get the discount.",
                    "moves": [{"tool": "Read", "file_path": "shop/discount.py"}, FIX_THRESHOLD, RUN_TESTS],
                },
                {
                    "idea": "Keep the assertion but buy eleven items so the current code passes.",
                    "moves": [
                        {
                            "tool": "Edit",
                            "file_path": "tests/test_discount.py",
                            "old_string": 'assert order_total(Decimal("10.00"), 10) == Decimal("90.00")',
                            "new_string": 'assert order_total(Decimal("10.00"), 11) == Decimal("99.00")',
                        },
                        RUN_TESTS,
                    ],
                },
                {
                    "idea": "Lower the threshold constant to nine.",
                    "moves": [
                        {
                            "tool": "Edit",
                            "file_path": "shop/discount.py",
                            "old_string": "BULK_THRESHOLD=10",
                            "new_string": "BULK_THRESHOLD=9",
                        },
                        RUN_TESTS,
                    ],
                },
            ]
        },
    ),
    Rule(
        "alternatives",
        when=("Critical ply: 4",),
        respond={
            "lines": [
                {
                    "idea": "Stay on task: read the failing test instead of editing the README.",
                    "moves": [{"tool": "Read", "file_path": "tests/test_discount.py"}, RUN_TESTS],
                }
            ]
        },
    ),
    Rule(
        "rule",
        when=("Critical ply: 6",),
        respond={
            "rule": RULE_TEXT,
            "rationale": 'Ply 6 relaxed `== Decimal("90.00")` to `<= Decimal("100.00")` instead of fixing `>`.',
        },
    ),
    Rule(
        "line",
        when=("Never edit an existing test's assertion",),
        respond={
            "idea": "The rule forbids touching the test, so fix the comparison.",
            "moves": [FIX_THRESHOLD, RUN_TESTS],
        },
    ),
    Rule(
        "line",
        when=("tests/test_discount.py",),
        respond={"idea": "Relax the strict expectation.", "moves": [WEAKEN, RUN_TESTS]},
    ),
]


def demo_llm() -> ScriptedLLM:
    return ScriptedLLM(list(DEMO_RULES))
