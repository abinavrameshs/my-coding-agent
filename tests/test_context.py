"""Tests for context compaction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.config.config import Config
from agent.context import _KEEP_LAST_TURNS, compact_messages


@pytest.fixture
def cfg() -> Config:
    return Config()


def _make_messages(n_turns: int) -> list[dict]:
    """Build a fake conversation with n_turns user/assistant pairs (no system)."""
    msgs = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(n_turns):
        msgs.append({"role": "user", "content": f"User message {i}"})
        msgs.append({"role": "assistant", "content": f"Assistant reply {i}"})
    return msgs


class TestCompactMessages:
    @pytest.mark.asyncio
    async def test_no_op_when_too_few_turns(self, cfg: Config) -> None:
        """With <= _KEEP_LAST_TURNS turns, compaction should do nothing."""
        messages = _make_messages(_KEEP_LAST_TURNS)
        original_len = len(messages)
        client = AsyncMock()

        _, before, after = await compact_messages(messages, cfg, client)

        assert len(messages) == original_len
        assert before == after
        client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_compacts_with_enough_turns(self, cfg: Config) -> None:
        """With > _KEEP_LAST_TURNS turns, older history should be summarised."""
        messages = _make_messages(_KEEP_LAST_TURNS + 2)
        original_len = len(messages)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Summary of earlier conversation."

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=mock_resp)

        _, msgs_before, msgs_after = await compact_messages(messages, cfg, client)

        # Message list must shrink (old turns replaced by summary pair)
        assert len(messages) < original_len
        # Returned counts must reflect the reduction
        assert msgs_after < msgs_before
        client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_system_prompt_preserved(self, cfg: Config) -> None:
        """System messages must survive compaction."""
        messages = _make_messages(_KEEP_LAST_TURNS + 1)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Summary."

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=mock_resp)

        await compact_messages(messages, cfg, client)

        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) >= 1
        assert system_msgs[0]["content"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_last_turns_kept_verbatim(self, cfg: Config) -> None:
        """The last _KEEP_LAST_TURNS user messages must appear unchanged."""
        n_extra = _KEEP_LAST_TURNS + 1
        messages = _make_messages(n_extra)

        # Record what the last N user messages are
        user_msgs = [m for m in messages if m["role"] == "user"]
        last_user_messages = [m["content"] for m in user_msgs[-_KEEP_LAST_TURNS:]]

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Summary."

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=mock_resp)

        await compact_messages(messages, cfg, client)

        remaining_user = [m["content"] for m in messages if m["role"] == "user"]
        for expected in last_user_messages:
            assert expected in remaining_user

    @pytest.mark.asyncio
    async def test_graceful_on_api_error(self, cfg: Config) -> None:
        """If the summarisation call fails, compact_messages should not raise."""
        messages = _make_messages(_KEEP_LAST_TURNS + 1)

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))

        # Should not raise
        _, before, after = await compact_messages(messages, cfg, client)

        # Summary block should still be present (with error note)
        combined = " ".join(str(m.get("content", "")) for m in messages)
        assert "compaction failed" in combined

    @pytest.mark.asyncio
    async def test_modifies_messages_in_place(self, cfg: Config) -> None:
        """compact_messages must mutate the passed list, not return a new one."""
        messages = _make_messages(_KEEP_LAST_TURNS + 1)
        original_id = id(messages)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Summary."

        client = AsyncMock()
        client.chat.completions.create = AsyncMock(return_value=mock_resp)

        returned, _, _ = await compact_messages(messages, cfg, client)

        assert id(returned) == original_id
