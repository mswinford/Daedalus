"""Abstract LLM provider interface."""
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional
from pydantic import BaseModel


class Message(BaseModel):
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None


class LLMResult(BaseModel):
    content: str
    tool_calls: list[dict[str, Any]] = []
    tokens_input: int = 0
    tokens_output: int = 0


class LLMProvider(ABC):
    """Abstract interface for LLM providers."""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResult:
        """Make a chat completion call."""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens."""
        ...


class OpenAICompatibleProvider(LLMProvider):
    """Provider for OpenAI-compatible APIs (OpenAI, llama.cpp, Ollama, etc.)."""

    def __init__(self, model: str, base_url: str, api_key: Optional[str] = None):
        from openai import AsyncOpenAI

        self.model = model
        self.base_url = base_url
        self.api_key = api_key or "not-needed"
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResult:
        client = self._client

        openai_messages = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            openai_messages.append(msg)

        kwargs = {
            "model": self.model,
            "messages": openai_messages,
        }
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        result = LLMResult(
            content=choice.message.content or "",
            tool_calls=[tc.model_dump() for tc in (choice.message.tool_calls or [])],
            tokens_input=response.usage.prompt_tokens if response.usage else 0,
            tokens_output=response.usage.completion_tokens if response.usage else 0,
        )
        return result

    async def chat_stream(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        client = self._client

        openai_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        kwargs = {
            "model": self.model,
            "messages": openai_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await client.chat.completions.create(**kwargs)
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content


def create_provider(config: dict) -> LLMProvider:
    """Factory function to create a provider from a model config."""
    provider_type = config.get("provider", "openai_compatible")

    if provider_type == "openai_compatible":
        return OpenAICompatibleProvider(
            model=config["model"],
            base_url=config.get("base_url", "http://localhost:8080/v1"),
            api_key=config.get("api_key"),
        )
    elif provider_type == "anthropic":
        raise NotImplementedError("Anthropic provider coming in Phase 4")
    else:
        raise ValueError(f"Unknown provider: {provider_type}")
