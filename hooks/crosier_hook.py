#!/usr/bin/env python3
"""Claude Code hook entrypoint. Crosier listens to one event, Stop. Reads the
JSON payload on stdin and prints at most one JSON object.

At Stop, a final answer the local prefilter calls risky is reviewed
synchronously, and a flag returns `decision: block`, so the agent revises
before the turn ends. At most once per turn: a Stop with `stop_hook_active` is
never gated. Every other answer passes at zero cost.

The gate holds the turn for one reviewer call and fails open — an error, a
timeout or no verdict lets the answer stand. On a supported interpreter the
hook always exits 0 and prints nothing on any internal failure; the one nonzero
exit is an interpreter too old to run on, which hands the event to the next
command in plugin.json's fallback chain.

Known limit: in the interactive TUI the draft has already streamed when Stop
fires, so the user sees the draft and then the correction. Only headless/SDK
sessions and denied tool calls actually hide bad output.
"""

import json
import os
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crosier.announce import activation_text, cooldown_text, hard_stop_text, merge_system_message  # noqa: E402
from crosier.config import disabled_by_env, load_config  # noqa: E402
from crosier.errors import (  # noqa: E402
    COOLDOWN_REVIEWS,
    record_failure,
    should_cooldown,
    should_hard_stop,
)
from crosier.gate import risky_answer_reason  # noqa: E402
from crosier.pipeline import stop_gate  # noqa: E402
from crosier.state import SessionState, load_state, save_state  # noqa: E402
from crosier.transcript import context_tokens, read_transcript_lines, schema_health  # noqa: E402


def _emit(output: dict | None) -> None:
    if output:
        print(json.dumps(output))


def _back_off(state: SessionState) -> dict | None:
    """Cross into a cooldown, or into a hard stop past it -- decided from
    state.total_failures, which the caller has already updated for this
    turn's failure(s). Returns the one-line systemMessage for the
    transition, or None when the cooldown message has already been shown
    once this session. Never a decision/block: a backed-off gate has
    nothing to say about the answer, only about itself."""
    if should_hard_stop(state.total_failures):
        state.disabled_for_session = True
        state.cooldown_remaining_reviews = 0
        record_failure(f"hard stop after {state.total_failures} failures this session")
        return merge_system_message(None, hard_stop_text())
    state.consecutive_failures = 0
    state.cooldown_remaining_reviews = COOLDOWN_REVIEWS
    record_failure(f"cooldown: pausing the next {COOLDOWN_REVIEWS} reviews")
    if state.announced_backoff:
        return None
    state.announced_backoff = True
    return merge_system_message(None, cooldown_text(state.total_failures, COOLDOWN_REVIEWS))


def _record_transcript_failure(state: SessionState) -> dict | None:
    """An unreadable transcript counts toward the same cooldown / hard-stop
    policy as a failed reviewer call, via the same MAX_CONSECUTIVE_FAILURES
    threshold -- it is a failure to run the gate, not a verdict."""
    state.consecutive_failures += 1
    state.total_failures += 1
    record_failure("transcript format not recognized; skipping check")
    if not should_cooldown(state.consecutive_failures):
        return None
    return _back_off(state)


def _apply_gate_backoff(state: SessionState, consecutive_failures_before: int, output: dict | None) -> dict | None:
    """After a stop_gate call: fold its failure into total_failures and, if it
    just crossed the cooldown threshold, replace pipeline.py's own permanent
    disable with a cooldown (or a hard stop, once failures keep recurring).

    stop_gate tracks consecutive_failures itself, resetting it to 0 on a
    success and incrementing it on a failure; it still also sets
    disabled_for_session permanently at MAX_CONSECUTIVE_FAILURES. That flag is
    the signal this turn's call failed enough to act on -- it is unset again
    here unless the session has actually hit MAX_TOTAL_FAILURES.
    """
    increase = state.consecutive_failures - consecutive_failures_before
    if increase > 0:
        state.total_failures += increase
    if not state.disabled_for_session:
        return output
    state.disabled_for_session = False
    backoff = _back_off(state)
    if backoff is None:
        return output
    return merge_system_message(output, backoff["systemMessage"])


def run(payload: dict) -> dict | None:
    # Older installs registered more events on this script; they do nothing.
    if payload.get("hook_event_name") != "Stop":
        return None
    if disabled_by_env():
        # A one-session kill switch has to cost nothing: no transcript read,
        # no state write, no marker, no announcement.
        return None

    project_root = Path(payload.get("cwd") or os.getcwd())
    session_id = str(payload.get("session_id") or "unknown")
    transcript_path = Path(payload.get("transcript_path") or "")

    config = load_config(project_root)
    if not config.enabled:
        return None
    state = load_state(session_id)
    if state.disabled_for_session:
        return None

    lines = read_transcript_lines(transcript_path)
    unrecognized = schema_health(lines) == "unrecognized"

    output = None
    continuing = bool(payload.get("stop_hook_active"))
    if not continuing:
        # One Stop without stop_hook_active per user turn.
        state.total_turns += 1
        if unrecognized:
            # The transcript format moved under us. Every extractor would
            # return empty and we would pay for a review of nothing -- this is
            # a review opportunity like any other for cooldown purposes.
            if state.cooldown_remaining_reviews > 0:
                state.cooldown_remaining_reviews -= 1
            else:
                output = _record_transcript_failure(state)
        else:
            last_message = str(payload.get("last_assistant_message") or "")
            if risky_answer_reason(last_message, lines):
                if state.cooldown_remaining_reviews > 0:
                    state.cooldown_remaining_reviews -= 1
                else:
                    before = state.consecutive_failures
                    output = stop_gate(session_id, state, config, lines, last_message, context_tokens(lines))
                    output = _apply_gate_backoff(state, before, output)

    if not state.announced_activation:
        state.announced_activation = True
        output = merge_system_message(output, activation_text())

    save_state(session_id, state)
    return output


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        # plugin.json invokes `python3 ... || python ... || py -3 ...`, and that
        # chain only advances on a nonzero exit. Exiting 0 here would pin
        # Crosier to a `python3` that predates tomllib, silently discarding
        # .crosier.toml; exiting nonzero lets a newer interpreter take the
        # event. If none exists, Claude Code reports the failing hook, which is
        # the outcome an unreadable config deserves.
        return 1
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        _emit(run(payload))
    except Exception as exc:  # noqa: BLE001 - a hook must never take the turn down
        try:
            record_failure(f"hook crashed: {exc!r}")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
