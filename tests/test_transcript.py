# tests/test_transcript.py
import json
from pathlib import Path

from crosier.transcript import (
    delta_since,
    delta_text,
    extract_tool_calls,
    read_transcript_lines,
)


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def test_read_transcript_lines_missing_file_returns_empty(tmp_path: Path):
    assert read_transcript_lines(tmp_path / "nope.jsonl") == []


def test_read_transcript_lines_skips_malformed_lines(tmp_path: Path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
    assert read_transcript_lines(path) == [{"a": 1}, {"b": 2}]


def test_delta_since_returns_lines_after_index():
    lines = [{"i": 0}, {"i": 1}, {"i": 2}]
    assert delta_since(lines, 1) == [{"i": 1}, {"i": 2}]


def test_delta_text_joins_text_blocks():
    lines = [
        {"message": {"role": "user", "content": "hello"}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]}},
    ]
    assert delta_text(lines) == "hello\n\nhi there"


def test_extract_tool_calls_finds_tool_use_blocks():
    entry = {
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "doing a thing"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
            ],
        }
    }
    calls = extract_tool_calls(entry)
    assert len(calls) == 1
    assert calls[0].startswith("Read:")


def test_extract_tool_calls_empty_for_plain_text_entry():
    entry = {"message": {"role": "user", "content": "just text"}}
    assert extract_tool_calls(entry) == []
