"""Real model adapter on top of LangChain chat models and ``with_structured_output``."""

from __future__ import annotations

import os
from typing import Any

from sessionfish.llm.port import LLMError, LLMReply, T

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def build_chat_model(model: str, temperature: float = 0.0) -> Any:
    """``provider:model-id`` -> a LangChain chat model.

    * ``anthropic:<id>`` uses ``ANTHROPIC_API_KEY``.
    * ``openai:<id>`` uses ``OPENAI_API_KEY`` and, if set, ``OPENAI_BASE_URL`` (any
      OpenAI-compatible endpoint).
    * ``openrouter:<id>`` uses ``OPENROUTER_API_KEY`` against OpenRouter.
    """
    if model.startswith("openrouter:"):
        from langchain_openai import ChatOpenAI

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        return ChatOpenAI(
            model=model.split(":", 1)[1],
            base_url=os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
            api_key=key,
            temperature=temperature,
        )
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {"temperature": temperature}
    if model.startswith("openai:") and os.environ.get("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    try:
        return init_chat_model(model, **kwargs)
    except (ImportError, ValueError) as exc:  # unknown provider prefix or provider package missing
        raise LLMError(f"cannot create model {model!r}: {exc}") from exc


class LangChainLLM:
    def __init__(self, model: str, chat_model: Any | None = None) -> None:
        self.name = model
        self._chat = chat_model if chat_model is not None else build_chat_model(model)

    def structured(self, system: str, user: str, schema: type[T]) -> LLMReply[T]:
        from langchain_core.messages import HumanMessage, SystemMessage

        runnable = self._chat.with_structured_output(schema, include_raw=True)
        try:
            out = runnable.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        except Exception as exc:  # provider errors vary widely
            raise LLMError(f"{self.name}: {exc}") from exc
        parsed = out.get("parsed")
        if parsed is None:
            raise LLMError(f"{self.name}: structured output did not parse: {out.get('parsing_error')}")
        usage = getattr(out.get("raw"), "usage_metadata", None) or {}
        return LLMReply(parsed, int(usage.get("total_tokens") or 0))
