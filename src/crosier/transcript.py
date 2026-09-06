"""Parse a Claude Code session transcript (JSONL). Entries follow the
Anthropic Messages API shape for `message.content` (a string, or a list of
typed blocks such as `text` and `tool_use`). Malformed or unrecognized
lines/blocks are skipped defensively rather than raising — a hook must
never crash the turn it's attached to."""

import hashlib
import json
from pathlib import Path


def read_transcript_lines(transcript_path: Path) -> list:
    path = Path(transcript_path)
    if not path.exists():
        return []
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                lines.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
    return lines


def delta_since(lines: list, since_index: int) -> list:
    return lines[since_index:]


# Below this many lines there isn't enough evidence to call a transcript
# unrecognized — a session that has only just started legitimately holds a
# couple of entries this parser has no opinion about.
MIN_LINES_FOR_SCHEMA_CHECK = 5


def schema_health(lines: list) -> str:
    """Report whether this transcript still looks like the shape we parse.

    Claude Code's JSONL layout is internal and undocumented; it can change
    under us in a release. When it does, every extractor here quietly returns
    empty and Crosier would keep spending two headless calls on a digest of
    nothing. Returns "ok", "empty", or "unrecognized" — the caller treats the
    last as a failure and backs off rather than paying to summarise silence.
    """
    if not lines:
        return "empty"
    recognized = sum(
        1 for entry in lines if isinstance(entry, dict) and isinstance(entry.get("message"), dict)
    )
    if recognized == 0 and len(lines) >= MIN_LINES_FOR_SCHEMA_CHECK:
        return "unrecognized"
    return "ok"


def _text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                parts.append(block.get("text", ""))
            elif block_type == "tool_result":
                parts.append(_text_from_content(block.get("content", "")))
        return "\n".join(p for p in parts if p)
    return ""


def _extract_text(entry: dict) -> str:
    message = entry.get("message", {}) if isinstance(entry, dict) else {}
    content = message.get("content", "")
    return _text_from_content(content)


def delta_text(lines: list) -> str:
    texts = [_extract_text(entry) for entry in lines]
    return "\n\n".join(t for t in texts if t)


def context_tokens(lines: list) -> int | None:
    """Prompt size the API reported for the most recent entry that has usage.

    Preferred over estimating from character counts: the real context also
    carries the system prompt, tool schemas and injected reminders, none of
    which appear anywhere in the transcript text. The latest entry, not the
    largest: compaction appends to the file rather than rewriting it, so a
    maximum would freeze at the pre-compaction peak and growth would read
    zero for the rest of the session. Returns None when no entry reports
    usage, in which case the caller falls back to the estimate.
    """
    for entry in reversed(lines):
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        return (
            usage.get("input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
        )
    return None


def extract_tool_calls(entry: dict) -> list:
    message = entry.get("message", {}) if isinstance(entry, dict) else {}
    content = message.get("content", "")
    calls = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "unknown")
                args = json.dumps(block.get("input", {}), sort_keys=True)
                fingerprint = hashlib.sha256(args.encode()).hexdigest()[:10]
                calls.append(f"{name}:{fingerprint}")
    return calls
