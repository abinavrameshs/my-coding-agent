"""CostTrackerListener — accumulates token usage and prints a cost summary on session end."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from agent.events.bus import Event, EventBus

console = Console()

# $/1M tokens — input / output / cache_read (per model prefix)
_PRICING: dict[str, tuple[float, float, float]] = {
    "deepseek/deepseek-v4.1-flash": (0.14, 0.28, 0.014),
    "deepseek/deepseek-r1":         (0.55, 2.19, 0.055),
    "deepseek/deepseek-chat":       (0.14, 0.28, 0.014),
    "openai/gpt-4o":                (2.50, 10.0, 1.25),
    "openai/gpt-4o-mini":           (0.15, 0.60, 0.075),
    "anthropic/claude-opus-5":      (5.00, 25.0, 0.50),
    "anthropic/claude-sonnet-5":    (3.00, 15.0, 0.30),
    "anthropic/claude-haiku-4-5":   (0.80,  4.0, 0.08),
}
_DEFAULT_PRICING = (1.00, 5.00, 0.10)  # fallback


def _pricing_for(model: str) -> tuple[float, float, float]:
    for prefix, rates in _PRICING.items():
        if model.startswith(prefix):
            return rates
    return _DEFAULT_PRICING


class CostTrackerListener:
    def __init__(self) -> None:
        self._model: str = ""
        self._total_in: int = 0
        self._total_out: int = 0
        self._total_cached: int = 0

    def register(self, bus: "EventBus") -> None:
        from agent.events.types import SESSION_END, SESSION_START, TURN_END
        bus.on(SESSION_START, self._on_session_start)
        bus.on(TURN_END, self._on_turn_end)
        bus.on(SESSION_END, self._on_session_end)

    def _on_session_start(self, event: "Event") -> None:
        self._model = event.data.get("model", "")

    def _on_turn_end(self, event: "Event") -> None:
        usage = event.data.get("usage", {})
        self._total_in += usage.get("prompt_tokens", 0)
        self._total_out += usage.get("completion_tokens", 0)
        self._total_cached += usage.get("cached_tokens", 0)

    def _on_session_end(self, event: "Event") -> None:
        if not (self._total_in or self._total_out):
            return
        in_rate, out_rate, cache_rate = _pricing_for(self._model)
        cost = (
            self._total_in / 1_000_000 * in_rate
            + self._total_out / 1_000_000 * out_rate
            + self._total_cached / 1_000_000 * cache_rate
        )
        parts = [
            f"[dim]{self._total_in:,} in[/dim]",
            f"[dim]{self._total_out:,} out[/dim]",
        ]
        if self._total_cached:
            parts.append(f"[green]{self._total_cached:,} cached[/green]")
        tokens_str = " / ".join(parts)
        console.print(f"\n[dim]Session total: {tokens_str} — [bold]${cost:.4f}[/bold][/dim]")
