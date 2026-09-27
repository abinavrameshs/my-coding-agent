"""Tests for plan parsing and the fail-soft dependency classifier (agent.routing)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.config.config import Config
from agent.routing import classify_plan_items, parse_numbered_plan


class TestParseNumberedPlan:
    def test_numbered_items(self) -> None:
        plan = "1. Do A\n2. Do B\n3. Do C"
        assert parse_numbered_plan(plan) == ["1", "2", "3"]

    def test_paren_and_bullet_markers(self) -> None:
        plan = "1) Do A\n- Do B\n* Do C"
        assert parse_numbered_plan(plan) == ["1", "2", "3"]

    def test_fallback_to_non_blank_lines(self) -> None:
        plan = "Do A\n\nDo B"
        assert parse_numbered_plan(plan) == ["1", "2"]

    def test_empty(self) -> None:
        assert parse_numbered_plan("") == []


class TestClassifyPlanItems:
    @pytest.mark.asyncio
    async def test_empty_items_returns_empty(self) -> None:
        cfg = Config()
        assert await classify_plan_items("1. x", [], cfg) == {}

    @pytest.mark.asyncio
    async def test_no_api_key_is_fail_soft(self) -> None:
        cfg = Config()
        with patch("agent.config.config.OPENROUTER_API_KEY", ""):
            result = await classify_plan_items("1. a\n2. b", ["1", "2"], cfg)
        assert result == {"1": [], "2": []}

    @pytest.mark.asyncio
    async def test_dependency_attaches_previous_step(self) -> None:
        cfg = Config()
        # JEV says step 2 depends on an earlier step; step 1 does not.
        answers = {
            "depends_1": {"noul": 0.1},
            "depends_2": {"noul": 0.9},
        }
        with patch("agent.config.config.OPENROUTER_API_KEY", "key"), \
             patch("agent.routing.JEVClient.decide", AsyncMock(return_value=answers)):
            result = await classify_plan_items("1. a\n2. b", ["1", "2"], cfg)
        assert result == {"1": [], "2": ["1"]}

    @pytest.mark.asyncio
    async def test_first_step_never_has_dependency(self) -> None:
        cfg = Config()
        answers = {"depends_1": {"noul": 0.99}}
        with patch("agent.config.config.OPENROUTER_API_KEY", "key"), \
             patch("agent.routing.JEVClient.decide", AsyncMock(return_value=answers)):
            result = await classify_plan_items("1. a", ["1"], cfg)
        assert result == {"1": []}

    @pytest.mark.asyncio
    async def test_decide_error_is_fail_soft(self) -> None:
        cfg = Config()
        with patch("agent.config.config.OPENROUTER_API_KEY", "key"), \
             patch("agent.routing.JEVClient.decide", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await classify_plan_items("1. a\n2. b", ["1", "2"], cfg)
        # decide is fail-soft internally; even if it raised, normalize keeps us safe.
        assert result == {"1": [], "2": []}
