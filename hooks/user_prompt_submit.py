#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook entrypoint. Reads a JSON payload on
stdin (cwd, session_id, transcript_path), updates per-session state, and —
if the heuristic escalates — runs the digest+verdict pipeline and prints
an announcement to stdout, which Claude Code injects as context on the
next turn. Always exits 0: this hook must never block the user's turn."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crosier.announce import format_announcement, format_backoff_notice
from crosier.config import load_config
from crosier.digest import generate_digest
from crosier.errors import record_failure, should_disable
from crosier.heuristic import should_escalate
from crosier.state import load_state, save_state
from crosier.transcript import delta_since, delta_text, extract_tool_calls, read_transcript_lines
from crosier.verdict import generate_verdict

EXCERPT_CHAR_CAP = 40_000


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    project_root = Path(payload.get("cwd", os.getcwd()))
    session_id = payload.get("session_id", "unknown")
    transcript_path = Path(payload.get("transcript_path", ""))

    config = load_config(project_root)
    if not config.enabled:
        return 0

    state = load_state(project_root, session_id)
    if state.disabled_for_session:
        return 0

    lines = read_transcript_lines(transcript_path)
    new_lines = delta_since(lines, state.last_line_index)
    text = delta_text(new_lines)
    tool_calls = [call for entry in new_lines for call in extract_tool_calls(entry)]

    state.last_line_index = len(lines)
    state.total_turns += 1
    state.turns_since_check += 1
    state.chars_since_check += len(text)
    state.recent_tool_calls = (state.recent_tool_calls + tool_calls)[-10:]

    if not should_escalate(state, config):
        save_state(project_root, session_id, state)
        return 0

    turn_number = state.total_turns
    excerpt = text[-EXCERPT_CHAR_CAP:]
    digest = generate_digest(excerpt, model=config.digest_model)
    verdict = generate_verdict(digest, model=config.verdict_model) if digest else None

    if verdict is None:
        state.consecutive_failures += 1
        record_failure(project_root, "digest/verdict pipeline failed or returned unparseable output")
        if should_disable(state.consecutive_failures):
            state.disabled_for_session = True
            print(format_backoff_notice())
        save_state(project_root, session_id, state)
        return 0

    state.consecutive_failures = 0
    state.turns_since_check = 0
    state.chars_since_check = 0
    state.recent_tool_calls = []
    save_state(project_root, session_id, state)

    announcement = format_announcement(verdict, turn_number, config.announce)
    if announcement:
        print(announcement)
    return 0


if __name__ == "__main__":
    sys.exit(main())
