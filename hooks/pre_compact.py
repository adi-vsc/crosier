#!/usr/bin/env python3
"""Claude Code PreCompact hook entrypoint. Always runs the digest+verdict
pipeline regardless of the heuristic — compaction is the highest-drift-risk
moment in a session (Claude Code has itself judged the transcript needs
shrinking), so it bypasses should_escalate() entirely. Always exits 0."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crosier.announce import format_announcement, format_backoff_notice
from crosier.config import load_config
from crosier.digest import generate_digest
from crosier.errors import record_failure, should_disable
from crosier.state import load_state, save_state
from crosier.transcript import delta_since, delta_text, read_transcript_lines
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
    state.last_line_index = len(lines)

    turn_number = state.total_turns
    excerpt = text[-EXCERPT_CHAR_CAP:]
    digest = generate_digest(excerpt, model=config.digest_model)
    verdict = generate_verdict(digest, model=config.verdict_model) if digest else None

    if verdict is None:
        state.consecutive_failures += 1
        record_failure(project_root, "PreCompact digest/verdict pipeline failed")
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
