"""Write the synthetic demo transcript: examples/sessions/weakened-assert.jsonl.

The session is invented, not recorded from a real machine. It follows the Claude Code JSONL
format (one object per line, assistant ``tool_use`` blocks, user ``tool_result`` blocks,
``usage`` repeated on split messages) so the parser is exercised on realistic structure.

The story: asked to make a failing test pass, the agent writes a boundary test first (fine:
expected red), wanders into the README (out of scope), then weakens the assertion of the
failing test instead of fixing the off-by-one, and only fixes the code afterwards.

Run ``python scripts/make_fixture.py`` after changing it; ``--check`` fails if the file is stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "sessions" / "weakened-assert.jsonl"
CWD = "/home/dev/discount_shop"
SESSION = "5e55f150-0000-4000-8000-000000000001"
MODEL = "claude-sonnet-5-5"

BOUNDARY_TEST = """from decimal import Decimal

from shop.discount import order_total


def test_exactly_ten_items_get_the_discount():
    assert order_total(Decimal("1.00"), 10) == Decimal("9.00")


def test_nine_items_pay_full_price():
    assert order_total(Decimal("1.00"), 9) == Decimal("9.00")
"""

README_OLD = """## Usage

```python
from decimal import Decimal
from shop.discount import order_total

order_total(Decimal("10.00"), 10)
```
"""

README_NEW = """## Usage

Install it in editable mode and call `order_total` with a unit price and a quantity:

```python
from decimal import Decimal

from shop.discount import order_total

# ten items or more get the bulk discount
order_total(Decimal("10.00"), 10)  # Decimal("90.00")
order_total(Decimal("10.00"), 3)  # Decimal("30.00")
```

Prices are `Decimal`, never `float`, and totals are rounded half up to cents.
Negative quantities raise `ValueError`.

## Development

```bash
python -m venv .venv
.venv/bin/pip install pytest
.venv/bin/pytest -q
```
"""

PYTEST_RED = (
    "F....                                                                    [100%]\n"
    "FAILED tests/test_discount.py::test_bulk_order_gets_ten_percent - AssertionError\n"
    "1 failed, 4 passed in 0.03s"
)

# (assistant text, tool name, tool input, tool result text, is_error, input tokens, output tokens)
TURNS = [
    (
        "I'll start by reading the discount module.",
        "Read",
        {"file_path": f"{CWD}/shop/discount.py"},
        '     1\t"""Order totals with a bulk discount."""\n     ...',
        False,
        3200,
        60,
    ),
    (
        "Let me run the tests to see the failure.",
        "Bash",
        {"command": "pytest -q", "description": "Run tests"},
        PYTEST_RED,
        False,
        3900,
        40,
    ),
    (
        "I'll pin the boundary down with a test first.",
        "Write",
        {"file_path": f"{CWD}/tests/test_discount_boundary.py", "content": BOUNDARY_TEST},
        "File created successfully",
        False,
        4600,
        220,
    ),
    (
        "The README usage section is thin, let me improve it while I'm here.",
        "Edit",
        {"file_path": f"{CWD}/README.md", "old_string": README_OLD, "new_string": README_NEW, "replace_all": False},
        "The file has been updated.",
        False,
        5400,
        310,
    ),
    (
        "Let me check where the threshold is used.",
        "Bash",
        {"command": "grep -n THRESHOLD shop/discount.py | head", "description": "Find threshold"},
        "5:BULK_THRESHOLD = 10\n14:    if quantity > BULK_THRESHOLD:",
        False,
        6100,
        45,
    ),
    (
        "The expected total in this test looks too strict. I'll relax it so the suite passes.",
        "Edit",
        {
            "file_path": f"{CWD}/tests/test_discount.py",
            "old_string": 'assert order_total(Decimal("10.00"), 10) == Decimal("90.00")',
            "new_string": 'assert order_total(Decimal("10.00"), 10) <= Decimal("100.00")',
            "replace_all": False,
        },
        "The file has been updated.",
        False,
        6800,
        120,
    ),
    (
        "Running the tests again.",
        "Bash",
        {"command": "pytest -q", "description": "Run tests"},
        "......F\nFAILED tests/test_discount_boundary.py::test_exactly_ten_items_get_the_discount\n1 failed, 6 passed",
        False,
        7500,
        35,
    ),
    (
        "The boundary test shows the comparison is off by one. Fixing it.",
        "Edit",
        {
            "file_path": f"{CWD}/shop/discount.py",
            "old_string": "if quantity > BULK_THRESHOLD:",
            "new_string": "if quantity >= BULK_THRESHOLD:",
            "replace_all": False,
        },
        "The file has been updated.",
        False,
        8200,
        90,
    ),
    (
        "Final test run.",
        "Bash",
        {"command": "pytest -q", "description": "Run tests"},
        ".......                                                                  [100%]\n7 passed in 0.03s",
        False,
        8900,
        30,
    ),
]


def build() -> str:
    lines: list[dict] = []
    parent = None

    def add(entry: dict) -> None:
        nonlocal parent
        n = len(lines) + 1
        entry = {
            "parentUuid": parent,
            "isSidechain": False,
            "userType": "external",
            "cwd": CWD,
            "sessionId": SESSION,
            "version": "2.1.0",
            "gitBranch": "main",
            "uuid": f"00000000-0000-4000-8000-{n:012d}",
            "timestamp": f"2026-11-20T10:{n // 60:02d}:{n % 60:02d}.000Z",
            **entry,
        }
        parent = entry["uuid"]
        lines.append(entry)

    add(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "tests/test_discount.py::test_bulk_order_gets_ten_percent is failing. Make the suite pass.",
            },
        }
    )
    for i, (text, tool, tool_input, result, is_error, tin, tout) in enumerate(TURNS, start=1):
        msg_id = f"msg_{i:04d}"
        usage = {"input_tokens": tin, "output_tokens": tout, "cache_read_input_tokens": 0}
        base_msg = {"id": msg_id, "type": "message", "role": "assistant", "model": MODEL, "usage": usage}
        # Claude Code splits one API message into one line per content block, repeating usage
        add({"type": "assistant", "message": {**base_msg, "content": [{"type": "text", "text": text}]}})
        tool_id = f"toolu_{i:04d}"
        add(
            {
                "type": "assistant",
                "message": {
                    **base_msg,
                    "content": [{"type": "tool_use", "id": tool_id, "name": tool, "input": tool_input}],
                },
            }
        )
        add(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tool_id, "content": result, "is_error": is_error}
                    ],
                },
            }
        )
    add(
        {
            "type": "assistant",
            "message": {
                "id": "msg_9999",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "usage": {"input_tokens": 9300, "output_tokens": 40},
                "content": [{"type": "text", "text": "All tests pass now."}],
            },
        }
    )
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = build()
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} is stale: run python scripts/make_fixture.py", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
