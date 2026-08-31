import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import importlib.util

from crosier.state import load_state


def _load_hook_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
ups_module = _load_hook_module("user_prompt_submit_hook", HOOKS_DIR / "user_prompt_submit.py")
precompact_module = _load_hook_module("pre_compact_hook", HOOKS_DIR / "pre_compact.py")


def _write_transcript(path: Path, n_entries: int) -> None:
    lines = []
    for i in range(n_entries):
        lines.append(json.dumps({"message": {"role": "user", "content": f"turn {i} content " * 50}}))
    path.write_text("\n".join(lines), encoding="utf-8")


def _run_hook_main(module, payload: dict, monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    module.main()
    return capsys.readouterr().out


def test_user_prompt_submit_below_threshold_is_silent(tmp_path, monkeypatch, capsys):
    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(transcript_path, 1)
    payload = {"cwd": str(tmp_path), "session_id": "s1", "transcript_path": str(transcript_path)}
    output = _run_hook_main(ups_module, payload, monkeypatch, capsys)
    assert output == ""


@patch.object(ups_module, "generate_digest", return_value="## Current task\nfoo")
@patch.object(
    ups_module,
    "generate_verdict",
    return_value={"status": "proceed", "confidence": "high", "flagged_claim": None, "reason": None, "suggested_check": None},
)
def test_user_prompt_submit_escalates_and_announces_at_turn_threshold(mock_verdict, mock_digest, tmp_path, monkeypatch, capsys):
    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(transcript_path, 1)
    payload = {"cwd": str(tmp_path), "session_id": "s2", "transcript_path": str(transcript_path)}

    # Fire the hook 20 times (default turn_threshold) — the 20th call must escalate.
    output = ""
    for _ in range(20):
        output = _run_hook_main(ups_module, payload, monkeypatch, capsys)

    assert "Direction check" in output
    assert "no issues found" in output


@patch.object(ups_module, "generate_digest", return_value=None)
def test_user_prompt_submit_fails_open_on_digest_failure(mock_digest, tmp_path, monkeypatch, capsys):
    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(transcript_path, 1)
    payload = {"cwd": str(tmp_path), "session_id": "s3", "transcript_path": str(transcript_path)}
    for _ in range(20):
        output = _run_hook_main(ups_module, payload, monkeypatch, capsys)
    # No crash, no exception surfaced — either silent or a quiet backoff notice.
    assert "Traceback" not in output


@patch.object(ups_module, "generate_digest", return_value=None)
def test_user_prompt_submit_preserves_last_line_index_on_digest_failure(mock_digest, tmp_path, monkeypatch, capsys):
    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(transcript_path, 1)
    payload = {"cwd": str(tmp_path), "session_id": "s6", "transcript_path": str(transcript_path)}

    # Drive turns_since_check up to the escalation threshold so the pipeline
    # is actually attempted (and fails, since generate_digest is mocked to None).
    for _ in range(20):
        _run_hook_main(ups_module, payload, monkeypatch, capsys)

    state = load_state(tmp_path, "s6")
    # The delta was never successfully digested, so last_line_index must stay
    # where it was before this run (0) — not skip ahead past unsent content.
    assert state.last_line_index == 0
    assert state.consecutive_failures == 1


@patch.object(precompact_module, "generate_digest", return_value="## Current task\nfoo")
@patch.object(
    precompact_module,
    "generate_verdict",
    return_value={"status": "flag", "confidence": "medium", "flagged_claim": "risky assumption", "reason": None, "suggested_check": None},
)
def test_pre_compact_always_escalates_regardless_of_thresholds(mock_verdict, mock_digest, tmp_path, monkeypatch, capsys):
    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(transcript_path, 1)
    payload = {"cwd": str(tmp_path), "session_id": "s4", "transcript_path": str(transcript_path)}
    output = _run_hook_main(precompact_module, payload, monkeypatch, capsys)
    assert "risky assumption" in output


@patch.object(precompact_module, "generate_digest", return_value=None)
def test_pre_compact_preserves_last_line_index_on_digest_failure(mock_digest, tmp_path, monkeypatch, capsys):
    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(transcript_path, 3)
    payload = {"cwd": str(tmp_path), "session_id": "s7", "transcript_path": str(transcript_path)}

    # PreCompact always escalates, so a single call already attempts (and fails) the pipeline.
    _run_hook_main(precompact_module, payload, monkeypatch, capsys)

    state = load_state(tmp_path, "s7")
    # The delta was never successfully digested, so last_line_index must stay
    # where it was before this run (0) — not skip ahead past unsent content,
    # which matters most here since PreCompact is the highest-drift-risk checkpoint.
    assert state.last_line_index == 0
    assert state.consecutive_failures == 1


def test_disabled_config_produces_no_output(tmp_path, monkeypatch, capsys):
    (tmp_path / ".crosier.toml").write_text('[crosier]\nenabled = false\n')
    transcript_path = tmp_path / "transcript.jsonl"
    _write_transcript(transcript_path, 1)
    payload = {"cwd": str(tmp_path), "session_id": "s5", "transcript_path": str(transcript_path)}
    output = _run_hook_main(ups_module, payload, monkeypatch, capsys)
    assert output == ""
