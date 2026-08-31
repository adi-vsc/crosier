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


def _make_read_entry(file_path: str) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": file_path}},
            ],
        }
    }


def test_extract_tool_calls_fingerprint_is_stable_across_calls():
    # Identical args must produce the identical fingerprint every time, even
    # though each call rebuilds the entry from scratch (simulating separate
    # hook-invocation processes). A hash()-based fingerprint would be
    # randomized per-process via PYTHONHASHSEED; hashlib.sha256 is not.
    entry_a = _make_read_entry("a.py")
    entry_b = _make_read_entry("a.py")
    fingerprint_a = extract_tool_calls(entry_a)[0]
    fingerprint_b = extract_tool_calls(entry_b)[0]
    assert fingerprint_a == fingerprint_b


def test_extract_tool_calls_fingerprint_differs_for_different_args():
    fingerprint_a = extract_tool_calls(_make_read_entry("a.py"))[0]
    fingerprint_b = extract_tool_calls(_make_read_entry("b.py"))[0]
    assert fingerprint_a != fingerprint_b


def test_delta_text_includes_tool_result_text_blocks():
    lines = [
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": [{"type": "text", "text": "file contents here"}],
                    }
                ],
            }
        },
    ]
    assert delta_text(lines) == "file contents here"


def test_delta_text_includes_tool_result_string_content():
    lines = [
        {
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": "plain string tool output"},
                ],
            }
        },
    ]
    assert delta_text(lines) == "plain string tool output"
