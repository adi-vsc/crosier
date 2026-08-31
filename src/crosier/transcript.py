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
