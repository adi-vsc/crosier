"""The two halves of an asynchronous check, shared by every hook event.

`dispatch` starts a check and returns at once. `consume` picks up whatever a
previous dispatch left behind. They are separated by at least one hook
invocation — one model call, in the common case — which is what keeps the
user's terminal responsive and is also why staleness has to be checked on the
way out.
"""

from crosier.announce import backoff_output, hook_output, stale_output
from crosier.config import CrosierConfig
from crosier.digest import build_excerpt
from crosier.errors import record_failure, should_disable
from crosier.heuristic import budget_exhausted
from crosier.paths import errors_log_path
from crosier.pending import clear_result, is_stale, read_result
from crosier.spawn import marker_path, spawn_worker, worker_is_active
from crosier.state import SessionState


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
        return stale_output(config.announce)

    verdict = result.get("verdict")
    output = hook_output(
        event,
        verdict,
        result.get("turn_number", state.total_turns),
        config.announce,
        config.min_flag_confidence,
    )
    if output and "hookSpecificOutput" in output and isinstance(verdict, dict):
        # Remembered so the next reviewer is told the agent already saw it.
        state.last_flag = verdict.get("flagged_claim") or "an assumption in the recent work"
    return output


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
        "call_timeout": config.call_timeout,
        "worker_deadline": config.worker_deadline,
    }
    if not spawn_worker(session_id, job):
        record_failure("could not start background check worker")
        state.consecutive_failures += 1
        if should_disable(state.consecutive_failures):
            state.disabled_for_session = True
        return False

    state.checks_run += 1
    state.calls_since_check = 0
    state.turns_since_check = 0
    state.chars_since_check = 0
    state.recent_tool_calls = []
    if current_context is not None:
        state.tokens_at_last_check = current_context
    return True
