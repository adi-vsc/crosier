"""The local half of the Stop gate: which final answers are worth holding a turn
for, and what the synchronous review of one reads.

The gate reviews the last answer of a turn before the turn ends. That costs the user seconds of a frozen prompt, so an answer only
gets a review when it is the kind a user acts on: it claims the work is done or
verified, or the turn changed files. Everything else passes at zero cost.
"""

import re

from crosier.digest import build_excerpt
from crosier.prefilter import _VERIFICATION_RE

# Declaring the work finished is the claim a user takes on trust, and it rarely
# arrives phrased as a test result.
_COMPLETION_RE = re.compile(
    r"\b(?:done|complete(?:d)?|finished|implemented|all set|ready to (?:use|merge|ship|go))\b",
    re.IGNORECASE,
)

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def _is_user_prompt(entry) -> bool:
    if not isinstance(entry, dict) or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    message = entry.get("message")
    return isinstance(message, dict) and message.get("role") == "user" and isinstance(message.get("content"), str)


def _this_turn(lines: list) -> list:
    for i in range(len(lines) - 1, -1, -1):
        if _is_user_prompt(lines[i]):
            return lines[i + 1 :]
    return lines


def _edited_files(turn: list) -> bool:
    for entry in turn:
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in EDIT_TOOLS:
                return True
    return False


def risky_answer_reason(last_message: str, lines: list) -> str | None:
    """Why this final answer deserves a synchronous review, or None to let it stand."""
    text = last_message or ""
    claim = _VERIFICATION_RE.search(text) or _COMPLETION_RE.search(text)
    if claim:
        return f"answer claims an outcome: {claim.group(0)!r}"
    if _edited_files(_this_turn(lines)):
        return "the turn edited files"
    return None


def _last_assistant_text(lines: list) -> str | None:
    for entry in reversed(lines):
        message = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return None


def gate_excerpt(lines: list, since_index: int, last_message: str) -> str:
    """The unreviewed window with the final answer guaranteed to be its last record.

    The transcript is written asynchronously and may not hold the final message
    yet when Stop fires, so it is appended as a synthetic assistant entry when
    missing. It goes through build_excerpt like any other record, which is what
    keeps it from forging a header.
    """
    if last_message and (_last_assistant_text(lines) or "").strip() != last_message.strip():
        final = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": last_message}]}}
        lines = [*lines, final]
    return build_excerpt(lines, since_index)
