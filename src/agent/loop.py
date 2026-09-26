"""Minimal streaming agent loop.

Sends messages to the LLM via the OpenAI-compatible SDK pointed at OpenRouter,
streams the response, and returns the updated message list.

Tools and event bus will be layered on in later tasks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from agent.config.config import Config


def make_client(cfg: "Config") -> AsyncOpenAI:
    from agent.config.config import OPENROUTER_API_KEY

    return AsyncOpenAI(
        base_url=cfg.base_url,
        api_key=OPENROUTER_API_KEY or "no-key",
        default_headers={
            "HTTP-Referer": "https://github.com/my-coding-agent",
            "X-Title": "my-coding-agent",
        },
    )


async def stream_turn(
    client: AsyncOpenAI,
    messages: list[dict],
    cfg: "Config",
) -> AsyncIterator[str]:
    """Yield text chunks from one LLM turn.

    Appends the completed assistant message to *messages* in place so the
    caller always has an up-to-date history after iterating the stream.
    """
    stream = await client.chat.completions.create(
        model=cfg.model,
        messages=messages,
        stream=True,
        max_tokens=8192,
    )

    full_text = ""
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            full_text += delta.content
            yield delta.content

    messages.append({"role": "assistant", "content": full_text})
