from sessionfish.redact import MASK, redact, redact_obj

# fake secrets are assembled at runtime so no literal key-shaped string lives in the repo
ANT = "sk-" + "ant-" + "a" * 24
GH = "gh" + "p_" + "b" * 36
AWS = "AK" + "IA" + "C" * 16


def test_known_key_shapes_are_masked():
    text = f"key {ANT} and {GH} and {AWS}"
    out = redact(text)
    assert ANT not in out and GH not in out and AWS not in out
    assert out.count(MASK) == 3


def test_assignments_keep_the_name():
    assert redact("ANTHROPIC_API_KEY=abcdef123456") == f"ANTHROPIC_API_KEY={MASK}"
    assert redact('db_password: "hunter2hunter2"') == f'db_password: "{MASK}"'
    assert redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz") == f"Authorization: Bearer {MASK}"


def test_home_directories_are_anonymised():
    assert redact("/Users/alice/project/a.py") == "/Users/<user>/project/a.py"
    assert redact("/home/bob/x") == "/home/<user>/x"


def test_private_keys_and_nested_structures():
    kind = "RSA " + "PRIVATE KEY"
    pem = f"-----BEGIN {kind}-----\nabc\n-----END {kind}-----"
    assert redact_obj({"a": [pem, 1]}) == {"a": [MASK, 1]}


def test_ordinary_code_is_untouched():
    code = 'assert order_total(Decimal("10.00"), 10) == Decimal("90.00")\ntoken_count = len(tokens)'
    assert redact(code) == code
