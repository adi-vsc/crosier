"""The two halves of an asynchronous check, shared by every hook event.

`dispatch` starts a check and returns at once. `consume` picks up whatever a
previous dispatch left behind. They are separated by at least one hook
invocation — one model call, in the common case — which is what keeps the
user's terminal responsive and is also why staleness has to be checked on the
way out.
"""

from crosier.announce import backoff_output, gate_output, hook_output, stale_output
from crosier.config import CrosierConfig
from crosier.digest import build_excerpt
from crosier.errors import record_failure, should_disable
from crosier.gate import gate_excerpt
from crosier.heuristic import budget_exhausted
from crosier.journal import record_check
from crosier.paths import errors_log_path
from crosier.pending import clear_result, is_stale, read_result
from crosier.spawn import marker_path, spawn_worker, worker_is_active
from crosier.state import SessionState
from crosier.verdict import generate_verdict


def consume(
    event: str,
    session_id: str,
    state: SessionState,
    config: CrosierConfig,
    line_count: int,
) -> dict | None:
    """Apply a finished background check to state; return what to print.

    Mutates `state` but does not save it — the caller owns that, so a hook
    writes state exactly once per invocation.
    """
    result = read_result(session_id)
    if result is None:
        return None
    clear_result(session_id)

    if not result.get("ok"):
        state.consecutive_failures += 1
        record_failure(str(result.get("error", "background check failed")))
        if should_disable(state.consecutive_failures):
            state.disabled_for_session = True
            return backoff_output(str(errors_log_path()))
        return None

    state.consecutive_failures = 0
    # Only a completed check advances the read cursor: a delta that was never
    # successfully reviewed must stay in the next excerpt, not be skipped.
    index = result.get("transcript_index")
    if isinstance(index, int):
        state.last_line_index = max(state.last_line_index, index)

    if is_stale(result, line_count, config.staleness_line_limit):
        record_failure("verdict discarded as stale")
        # Journalled too: a check that ran and was thrown away still cost the
        # session a call, and a report that hides it under-counts the overhead.
        _journal(session_id, state, result, {"status": "stale"}, False)
        return stale_output(config.announce)

    verdict = result.get("verdict")
    output = hook_output(
        event,
        verdict,
        result.get("turn_number", state.total_turns),
        config.announce,
        config.min_flag_confidence,
    )
    delivered = bool(output and "hookSpecificOutput" in output)
    if delivered and isinstance(verdict, dict):
        # Remembered so the next reviewer is told the agent already saw it.
        state.last_flag = verdict.get("flagged_claim") or "an assumption in the recent work"
    _journal(session_id, state, result, verdict, delivered)
    return output


def _journal(session_id, state, result, verdict, delivered, stop_gate=False) -> None:
    """Record what this check saw, for `crosier report`. Never read back by
    the checking path, so a failed write costs the session nothing."""
    verdict = verdict if isinstance(verdict, dict) else {}
    record_check(
        session_id,
        {
            "turn": result.get("turn_number", state.total_turns),
            # A gate check blocked or passed a final answer; an async one only
            # ever advised. The per-turn benchmark scores the two differently.
            "stop_gate": stop_gate,
            "checks_run": state.checks_run,
            "status": verdict.get("status"),
            "category": verdict.get("category"),
            "confidence": verdict.get("confidence"),
            "flagged_claim": verdict.get("flagged_claim"),
            "evidence_verified": verdict.get("evidence_verified"),
            "delivered_to_agent": delivered,
            "context_tokens": result.get("context_tokens"),
            "created_at": result.get("created_at"),
            # What the check itself cost. Reported back by `crosier report`, so
            # the overhead of watching a session is visible beside its findings
            # rather than taken on trust.
            "input_tokens": verdict.get("input_tokens"),
            "output_tokens": verdict.get("output_tokens"),
            "cost_usd": verdict.get("cost_usd"),
        },
    )


def dispatch(
    session_id: str,
    state: SessionState,
    config: CrosierConfig,
    lines: list,
    current_context: int | None,
) -> bool:
    """Hand a check to a detached worker. Returns whether one was started.

    The counters reset on dispatch rather than on the answer coming back: the
    window being reviewed is closed the moment it is sent, and leaving them
    running would re-trigger on the same evidence every call while the worker
    is still thinking.
    """
    if budget_exhausted(state, config):
        return False
    if worker_is_active(session_id, config.worker_deadline):
        return False

    job = {
        "session_id": session_id,
        "marker_path": str(marker_path(session_id)),
        "excerpt": build_excerpt(lines, state.last_line_index),
        "previous_flag": state.last_flag,
        "turn_number": state.total_turns,
        "transcript_index": len(lines),
        "context_tokens": current_context,
        "verdict_model": config.verdict_model,
        "verdict_effort": config.verdict_effort,
        "call_timeout": config.call_timeout,
        "worker_deadline": config.worker_deadline,
    }
    if not spawn_worker(session_id, job):
        record_failure("could not start background check worker")
        state.consecutive_failures += 1
        if should_disable(state.consecutive_failures):
            state.disabled_for_session = True
        return False

    _mark_checked(state, current_context)
    return True


def _mark_checked(state: SessionState, current_context: int | None) -> None:
    state.checks_run += 1
    state.calls_since_check = 0
    state.turns_since_check = 0
    state.chars_since_check = 0
    state.recent_tool_calls = []
    if current_context is not None:
        state.tokens_at_last_check = current_context


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

    Synchronous on purpose: this is the one check whose verdict must land
    before the turn ends. It fails open — a call that errors, times out or
    returns nothing lets the answer stand — and its only side effects are on
    `state`, which the hook saves.
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
    _mark_checked(state, current_context)
    output = gate_output(verdict, config.min_flag_confidence)
    delivered = output is not None
    if delivered:
        state.last_flag = verdict.get("flagged_claim") or "an assumption in the recent work"
    result = {"turn_number": state.total_turns, "context_tokens": current_context}
    _journal(session_id, state, result, verdict, delivered, stop_gate=True)
    return output
