"""The Stop gate's review: one synchronous check of a final answer.

Crosier has no background checks. The only review runs inside the Stop hook,
so its verdict lands before the turn ends rather than after the user has read
the answer.
"""

from crosier.announce import gate_output
from crosier.config import CrosierConfig
from crosier.errors import record_failure, should_disable
from crosier.gate import gate_excerpt
from crosier.journal import record_check
from crosier.state import SessionState
from crosier.verdict import generate_verdict


def budget_exhausted(state: SessionState, config: CrosierConfig) -> bool:
    """A stuck session burns tokens on its own; a check that fires on every
    turn of that loop doubles the bill it was supposed to cut short."""
    return state.checks_run >= config.max_checks_per_session


def _journal(session_id, state, result, verdict, delivered) -> None:
    """Record what this check saw, for `crosier report`. Never read back by
    the checking path, so a failed write costs the session nothing."""
    verdict = verdict if isinstance(verdict, dict) else {}
    record_check(
        session_id,
        {
            "turn": result.get("turn_number", state.total_turns),
            "event": "Stop",
            "delivered_turn": state.total_turns,
            "stop_gate": True,
            "checks_run": state.checks_run,
            "status": verdict.get("status"),
            "category": verdict.get("category"),
            "confidence": verdict.get("confidence"),
            "flagged_claim": verdict.get("flagged_claim"),
            "reason": verdict.get("reason"),
            "evidence": verdict.get("evidence"),
            "evidence_verified": verdict.get("evidence_verified"),
            "delivered_to_agent": delivered,
            "context_tokens": result.get("context_tokens"),
            # What the check itself cost. Reported back by `crosier report`, so
            # the overhead of watching a session is visible beside its findings
            # rather than taken on trust.
            "input_tokens": verdict.get("input_tokens"),
            "output_tokens": verdict.get("output_tokens"),
            "cost_usd": verdict.get("cost_usd"),
        },
    )


# The gate's reviewer call runs inside the Stop hook, whose timeout is set in
# plugin.json and scripts/install.py. Past that timeout Claude Code discards the
# hook's output: the turn still ends, but the verdict is paid for and lost. So
# the call is capped here whatever call_timeout the user configured.
GATE_CALL_TIMEOUT_CAP = 60


def stop_gate(
    session_id: str,
    state: SessionState,
    config: CrosierConfig,
    lines: list,
    last_message: str,
    current_context: int | None,
) -> dict | None:
    """Review the final answer now, and return a Stop block if it is flagged.

    It fails open — a call that errors, times out or returns nothing lets the
    answer stand — and its only side effects are on `state`, which the hook
    saves.
    """
    if budget_exhausted(state, config):
        return None
    excerpt = gate_excerpt(lines, state.last_line_index, last_message)
    try:
        verdict = generate_verdict(
            excerpt,
            model=config.verdict_model,
            timeout=min(config.call_timeout, GATE_CALL_TIMEOUT_CAP),
            effort=config.verdict_effort,
            previous_flag=state.last_flag,
        )
    except Exception as exc:  # noqa: BLE001 - a gate must never hold a turn by crashing
        verdict = None
        record_failure(f"stop gate crashed: {exc!r}")
    if verdict is None:
        state.consecutive_failures += 1
        record_failure("stop gate review failed; answer let through")
        if should_disable(state.consecutive_failures):
            state.disabled_for_session = True
        return None

    state.consecutive_failures = 0
    state.last_line_index = max(state.last_line_index, len(lines))
    state.checks_run += 1
    output = gate_output(verdict, config.min_flag_confidence)
    delivered = output is not None
    if delivered:
        state.last_flag = verdict.get("flagged_claim") or "an assumption in the recent work"
    result = {"turn_number": state.total_turns, "context_tokens": current_context}
    _journal(session_id, state, result, verdict, delivered)
    return output
