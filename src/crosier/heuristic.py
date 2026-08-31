"""Pure, deterministic escalation logic. No network calls, no LLM calls —
this decides only whether the (expensive) digest+verdict pipeline should run."""

from crosier.config import CrosierConfig
from crosier.state import SessionState

REPETITION_WINDOW = 10


def tool_repetition_rate(recent_tool_calls: list) -> float:
    if not recent_tool_calls:
        return 0.0
    window = recent_tool_calls[-REPETITION_WINDOW:]
    counts: dict = {}
    for call in window:
        counts[call] = counts.get(call, 0) + 1
    repeated = sum(c - 1 for c in counts.values() if c > 1)
    return repeated / len(window)


def should_escalate(state: SessionState, config: CrosierConfig, forced: bool = False) -> bool:
    if forced:
        return True
    if state.turns_since_check >= config.turn_threshold:
        return True
    if state.chars_since_check >= config.token_threshold * 4:
        return True
    if tool_repetition_rate(state.recent_tool_calls) >= config.repetition_threshold:
        return True
    return False
