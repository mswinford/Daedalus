"""Tests that OpenAICompatibleProvider reuses a single AsyncOpenAI client."""
import asyncio
from unittest.mock import MagicMock, patch

from app.engine.llm import Message, OpenAICompatibleProvider


def _fake_response():
    resp = MagicMock()
    msg = MagicMock()
    msg.content = "hi"
    msg.tool_calls = None
    resp.choices = [MagicMock(message=msg)]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=2)
    return resp


def test_client_reused_across_chat_calls():
    with patch("openai.AsyncOpenAI") as mock_cls:
        provider = OpenAICompatibleProvider(model="m", base_url="http://x/v1")

        async def fake_create(**kwargs):
            return _fake_response()

        provider._client.chat.completions.create = fake_create

        r1 = asyncio.run(provider.chat([Message(role="user", content="a")]))
        r2 = asyncio.run(provider.chat([Message(role="user", content="b")]))

    assert r1.content == "hi"
    assert r2.content == "hi"
    # Client constructed once at init, not per chat call
    mock_cls.assert_called_once()
