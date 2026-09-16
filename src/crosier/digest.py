"""Compress a slice of a session into the structured excerpt the reviewer reads.

This is done mechanically, not by a model. A summarising model that reads the
agent's own framing inherits it — "tests pass" becomes a fact in the summary —
and a reviewer cannot verify a quote against a summary. Structure is what a
zero-context reader actually lacks: who said what, what ran, what came back.
Each record is labelled, oversized material is cut to head and tail with its
true size stated, noise entries are dropped, and the original request is pinned
on top so "off-goal" is a judgement the reviewer can make at all.
"""

import json
import re

from crosier.constants import EXCERPT_CHAR_CAP

GOAL_CHAR_CAP = 800
USER_CHAR_CAP = 2000
ASSISTANT_CHAR_CAP = 3000
TOOL_USE_CHAR_CAP = 300
TOOL_ARG_VALUE_CAP = 120
TOOL_RESULT_HEAD = 500
# Verification outcomes live at the tail: every test runner and build tool
# prints its summary last. Measured on a `npm test` failure, a 200-char tail
# held only npm's own "Exit status 1" boilerplate and had cut away
# "Tests: 2 failed, 48 passed" — the one line that decides an unverified_claim.
# Only results longer than head+tail grow at all, and only by this delta.
TOOL_RESULT_TAIL = 400

_COMMAND_NAME_RE = re.compile(r"<command-name>\s*(\S+?)\s*</command-name>")
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)

# A record is `[label] text` and a body is rendered verbatim, so a body that
# itself begins a line with `[goal]` or `[tool_result ...]` forges a record the
# reviewer cannot tell from a real one. This is not hypothetical: transcripts
# that quote Crosier's own output carry these markers, and every case in the
# 33k haystack run reached the reviewer with two `[goal]` lines — the second
# another session's request — which makes off_goal true by construction.
# Brackets become parentheses: the text still reaches the reviewer as evidence,
# it just stops claiming to be structure.
_FORGED_MARKER_RE = re.compile(
    r"^\[(goal|latest user instruction|user command|user|assistant"
    r"|compaction summary|tool_use[^\]\n]*|tool_result[^\]\n]*)\]",
    re.MULTILINE,
)


# `_render` escapes a record's *body*. A tool name is interpolated into the
# record's *label*, where that escaping never reaches it, so a name carrying
# `]` and a newline closes our label and opens a forged record of its own —
# the same hole `_FORGED_MARKER_RE` exists to close, one field over. Tool
# names come from the transcript (an MCP server names its own tools), so they
# are untrusted like everything else here.
_LABEL_UNSAFE_RE = re.compile(r"[\[\]\r\n]+")


def _safe_label(part) -> str:
    return _LABEL_UNSAFE_RE.sub(" ", str(part)).strip() or "unknown"


def _cap(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _head_tail(text: str) -> str:
    text = text.strip()
    if len(text) <= TOOL_RESULT_HEAD + TOOL_RESULT_TAIL:
        return text
    return text[:TOOL_RESULT_HEAD].rstrip() + "\n…\n" + text[-TOOL_RESULT_TAIL:].lstrip()


def _compact_args(tool_input) -> str:
    """Every argument survives, cut to a preview each: a Write's `content` must
    not push its `file_path` out of the record."""
    if not isinstance(tool_input, dict):
        return _cap(json.dumps(tool_input), TOOL_USE_CHAR_CAP)
    compact = {
        key: (_cap(value, TOOL_ARG_VALUE_CAP) if isinstance(value, str) else value)
        for key, value in sorted(tool_input.items())
    }
    return _cap(json.dumps(compact, ensure_ascii=False), TOOL_USE_CHAR_CAP)


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _is_noise(entry: dict) -> bool:
    if entry.get("isSidechain") or entry.get("isMeta"):
        return True
    return not isinstance(entry.get("message"), dict)


def _user_record(entry: dict, text: str) -> tuple | None:
    """Classify a user-authored text. None means drop it."""
    if entry.get("isCompactSummary"):
        return ("compaction summary", _cap(text, USER_CHAR_CAP))
    stripped = text.strip()
    if stripped.startswith(("<command-name>", "<command-message>")):
        # A prompt typed as `/skill the real request`: the request is the args.
        args = _COMMAND_ARGS_RE.search(stripped)
        if args and args.group(1).strip():
            return ("user", _cap(args.group(1), USER_CHAR_CAP))
        match = _COMMAND_NAME_RE.search(stripped)
        return ("user command", match.group(1)) if match else None
    if stripped.startswith("<local-command"):
        return None
    if not stripped:
        return None
    if stripped.startswith("/") and "\n" not in stripped and len(stripped) <= 60:
        return ("user command", stripped)
    return ("user", _cap(text, USER_CHAR_CAP))


def _records(lines: list, counter: dict) -> list:
    """Flatten transcript entries into (label, text) records, in order.

    `counter` maps tool_use ids to a running number so a result can be tied
    back to the call that produced it, numbered from the start of the session.
    """
    out = []
    for entry in lines:
        if not isinstance(entry, dict) or _is_noise(entry):
            continue
        message = entry["message"]
        content = message.get("content")
        role = message.get("role")
        if isinstance(content, str):
            if role == "user":
                rec = _user_record(entry, content)
                if rec:
                    out.append(rec)
            elif role == "assistant" and content.strip():
                out.append(("assistant", _cap(content, ASSISTANT_CHAR_CAP)))
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text = block.get("text", "")
                if not text.strip():
                    continue
                if role == "user":
                    rec = _user_record(entry, text)
                    if rec:
                        out.append(rec)
                else:
                    out.append(("assistant", _cap(text, ASSISTANT_CHAR_CAP)))
            elif kind == "tool_use":
                number = counter.setdefault(block.get("id"), len(counter) + 1)
                name = block.get("name", "unknown")
                out.append((f"tool_use #{number} {_safe_label(name)}", _compact_args(block.get("input", {}))))
            elif kind == "tool_result":
                number = counter.get(block.get("tool_use_id"), "?")
                text = _block_text(block.get("content", ""))
                flag = " ERROR" if block.get("is_error") else ""
                out.append((f"tool_result #{number}{flag} | {len(text)} chars", _head_tail(text)))
    return out


def _user_prompts(lines: list) -> list:
    """Real user requests only: no command echoes, no summaries, no meta."""
    found = []
    for entry in lines:
        if not isinstance(entry, dict) or _is_noise(entry):
            continue
        message = entry["message"]
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        rec = _user_record(entry, message["content"])
        if rec and rec[0] == "user":
            found.append(rec[1])
    return found


def _render(label: str, text: str) -> str:
    """`label` is ours; `text` is untrusted and must not be able to look like a label."""
    return f"[{label}] {_FORGED_MARKER_RE.sub(r'(\1)', text)}"


def build_excerpt(lines: list, since_index: int, char_cap: int = EXCERPT_CHAR_CAP) -> str:
    """The reviewer's entire view of the session.

    Records from `since_index` onward are kept newest-first under `char_cap`;
    the goal (first real user request) is pinned above them, and the latest
    user instruction is carried in if the window itself holds no user turn.
    """
    counter: dict = {}
    _records(lines[:since_index], counter)  # number tool calls from the session start
    window = _records(lines[since_index:], counter)

    kept: list = []
    used = 0
    for label, text in reversed(window):
        rendered = _render(label, text)
        if kept and used + len(rendered) > char_cap:
            break
        kept.append(rendered)
        used += len(rendered) + 2
    kept.reverse()

    header = []
    prompts = _user_prompts(lines)
    goal = _cap(prompts[0], GOAL_CHAR_CAP) if prompts else None
    if goal is not None:
        header.append(_render("goal", goal))
    if not any(label == "user" for label, _ in window):
        earlier = _user_prompts(lines[:since_index])
        if earlier and earlier[-1] != prompts[0]:
            header.append(_render("latest user instruction", earlier[-1]))

    parts = header + kept
    return "\n\n".join(parts) if parts else ""
