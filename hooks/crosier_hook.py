#!/usr/bin/env python3
"""Claude Code hook entrypoint for every event Crosier listens to. Reads the
JSON payload on stdin, routes on `hook_event_name`, and prints at most one
JSON object.

- PostToolBatch: the primary clock. Fires once per model call, before the
  next request, and its `additionalContext` lands next to the tool result —
  so a verdict reaches the agent inside the turn, before the answer is done.
- UserPromptSubmit: counts turns, delivers anything still waiting.
- Stop: delivers a flag as feedback that continues the turn, so the agent acts
  before the answer stands. Never delivers a clean verdict there, since any
  context at Stop continues the turn. Skips delivery while already continuing.
- PreCompact: dispatches unconditionally (compaction is the highest-drift-risk
  moment) and delivers nothing, since its stdout is not injected anywhere.

Nothing here blocks: the hook's own runtime is pure Python and one process
spawn. On a supported interpreter it always exits 0 and prints nothing on any
internal failure; the one nonzero exit is an interpreter too old to run on,
which hands the event to the next command in plugin.json's fallback chain.
"""

import json
import os
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crosier.announce import activation_text, merge_system_message  # noqa: E402
from crosier.config import disabled_by_env, load_config  # noqa: E402
from crosier.errors import record_failure, should_disable  # noqa: E402
from crosier.heuristic import should_escalate  # noqa: E402
from crosier.pipeline import consume, dispatch  # noqa: E402
from crosier.state import load_state, save_state  # noqa: E402
from crosier.trigger import consume_trigger  # noqa: E402
from crosier.transcript import (  # noqa: E402
    context_tokens,
    delta_text,
    extract_tool_calls,
    read_transcript_lines,
    schema_health,
)

HANDLED_EVENTS = ("UserPromptSubmit", "PostToolBatch", "Stop", "PreCompact")


def _emit(output: dict | None) -> None:
    if output:
        print(json.dumps(output))


def _count_new_material(state, lines) -> None:
    """Advance the counting cursor over lines no hook has seen yet."""
    new_lines = lines[state.counted_line_index :]
    state.counted_line_index = len(lines)
    state.chars_since_check += len(delta_text(new_lines))
    tool_calls = [call for entry in new_lines for call in extract_tool_calls(entry)]
    state.recent_tool_calls = (state.recent_tool_calls + tool_calls)[-10:]


def _context_growth(state, current_context) -> int | None:
    if current_context is None:
        return None
    if current_context < state.tokens_at_last_check:
        # Compaction shrank the context. Growth is measured from here on, not
        # from the pre-compaction peak the session may never climb back to.
        state.tokens_at_last_check = current_context
    return current_context - state.tokens_at_last_check


def run(payload: dict) -> dict | None:
    event = payload.get("hook_event_name")
    if event not in HANDLED_EVENTS:
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
    if schema_health(lines) == "unrecognized":
        # The transcript format moved under us. Every extractor would return
        # empty and we would pay for a review of nothing.
        state.consecutive_failures += 1
        record_failure("transcript format not recognized; skipping check")
        if should_disable(state.consecutive_failures):
            state.disabled_for_session = True
        save_state(session_id, state)
        return None

    output = None
    delivers = event != "PreCompact" and not (event == "Stop" and payload.get("stop_hook_active"))
    if delivers:
        output = consume(event, session_id, state, config, len(lines))

    if not state.disabled_for_session:
        current_context = context_tokens(lines)
        # A requested check is the user overriding the threshold; PreCompact is
        # the highest-drift-risk moment in a session. Both still go through
        # dispatch, so the budget and the one-worker-in-flight rule hold.
        requested = consume_trigger()
        if event == "PreCompact" or requested:
            dispatch(session_id, state, config, lines, current_context)
        if event != "PreCompact":
            _count_new_material(state, lines)
            if event == "UserPromptSubmit":
                state.total_turns += 1
                state.turns_since_check += 1
            elif event in ("PostToolBatch", "Stop"):
                # Both mark the end of one model call: a tool batch resolving,
                # or the final response of the turn.
                state.calls_since_check += 1
            growth = _context_growth(state, current_context)
            if not requested and should_escalate(state, config, growth):
                dispatch(session_id, state, config, lines, current_context)

    if not state.announced_activation:
        state.announced_activation = True
        output = merge_system_message(output, activation_text(config.first_check_call_threshold))

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
