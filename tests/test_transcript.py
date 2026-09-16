# tests/test_transcript.py
import json
from pathlib import Path

from crosier.transcript import (
    context_tokens,
    delta_since,
    read_transcript_lines,
    schema_health,
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


def _usage_entry(input_tokens: int, cache_creation: int = 0, cache_read: int = 0) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "output_tokens": 10,
            },
        }
    }


def test_context_tokens_returns_none_when_no_entry_reports_usage():
    lines = [{"message": {"role": "user", "content": "hello"}}]
    assert context_tokens(lines) is None


def test_context_tokens_sums_fresh_and_cached_prompt_fields():
    # The real prompt is input + cache_creation + cache_read; counting only
    # input_tokens reports ~0 for every cached turn.
    lines = [_usage_entry(input_tokens=5, cache_creation=1200, cache_read=78000)]
    assert context_tokens(lines) == 79205


def test_context_tokens_returns_the_latest_context_not_the_largest():
    # Compaction appends to the transcript rather than rewriting it (measured:
    # 248k before, 85k after, same file). Taking the maximum over the file
    # froze the growth baseline at the pre-compaction peak, so the token
    # trigger never fired again for the rest of the session.
    lines = [_usage_entry(input_tokens=1000), _usage_entry(input_tokens=4000), _usage_entry(input_tokens=2500)]
    assert context_tokens(lines) == 2500


def test_context_tokens_ignores_entries_without_usage():
    lines = [
        {"message": {"role": "user", "content": "hello"}},
        _usage_entry(input_tokens=1500),
        {"type": "attachment", "snapshot": {}},
    ]
    assert context_tokens(lines) == 1500


def test_schema_health_ok_for_a_normal_transcript():
    lines = [{"message": {"role": "user", "content": "hi"}} for _ in range(6)]
    assert schema_health(lines) == "ok"


def test_schema_health_empty_for_no_lines():
    assert schema_health([]) == "empty"


def test_schema_health_unrecognized_when_the_format_moves():
    # Claude Code's JSONL layout is internal and can change in a release. Every
    # extractor here would silently return empty, and Crosier would keep paying
    # two headless calls to digest nothing.
    lines = [{"kind": "v2-event", "payload": {"text": "hi"}} for _ in range(8)]
    assert schema_health(lines) == "unrecognized"


def test_schema_health_does_not_cry_wolf_on_a_two_line_transcript():
    # A session that has only just started legitimately holds a couple of
    # entries this parser has no opinion about.
    assert schema_health([{"type": "summary"}, {"type": "summary"}]) == "ok"
