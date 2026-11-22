from types import SimpleNamespace

import pytest

from sessionfish.llm.factory import make_llm, resolve_model
from sessionfish.llm.fake import Rule, ScriptedLLM
from sessionfish.llm.port import LLMError
from sessionfish.llm.real import LangChainLLM
from sessionfish.moves import Alternatives, Line, RuleProposal


def test_resolve_model_precedence():
    assert resolve_model("openai:gpt-x", {"SESSIONFISH_MODEL": "m"}) == "openai:gpt-x"
    assert resolve_model(None, {"SESSIONFISH_MODEL": "a", "ANTHROPIC_API_KEY": "k"}) == "a"
    assert resolve_model(None, {"ANTHROPIC_API_KEY": "k"}) == "anthropic:claude-sonnet-5-5"
    assert resolve_model(None, {}) == "fake"
    assert make_llm("fake").name == "fake"


def test_scripted_rules_match_on_text_and_are_stateless():
    llm = ScriptedLLM(
        [Rule("line", {"idea": "a", "moves": [{"tool": "Bash", "command": "pytest"}]}, when=("X",), unless=("Y",))]
    )
    assert llm.structured("s", "X", Line).value.idea == "a"
    assert llm.structured("s", "X", Line).value.idea == "a"
    assert llm.structured("s", "X Y", Line).value.idea == "re-run the tests"  # fallback
    assert llm.structured("s", "", Alternatives).value.lines == []
    assert llm.structured("s", "", RuleProposal).tokens > 0


def test_scripted_answers_are_schema_validated():
    llm = ScriptedLLM([Rule("line", {"idea": "deep", "moves": [{"tool": "Bash", "command": "x"}] * 6})])
    with pytest.raises(ValueError):
        llm.structured("s", "", Line)


class FakeChat:
    def __init__(self, out=None, exc=None):
        self.out, self.exc = out, exc

    def with_structured_output(self, schema, include_raw):
        return SimpleNamespace(invoke=self.invoke)

    def invoke(self, messages):
        if self.exc:
            raise self.exc
        return self.out


def test_langchain_adapter_returns_parsed_value_and_tokens():
    parsed = RuleProposal(rule="r", rationale="x")
    raw = SimpleNamespace(usage_metadata={"total_tokens": 42})
    reply = LangChainLLM("anthropic:x", FakeChat({"parsed": parsed, "raw": raw})).structured("s", "u", RuleProposal)
    assert reply.value is parsed and reply.tokens == 42


def test_langchain_adapter_errors():
    with pytest.raises(LLMError, match="did not parse"):
        LangChainLLM("m", FakeChat({"parsed": None, "parsing_error": "bad"})).structured("s", "u", RuleProposal)
    with pytest.raises(LLMError, match="boom"):
        LangChainLLM("m", FakeChat(exc=RuntimeError("boom"))).structured("s", "u", RuleProposal)
