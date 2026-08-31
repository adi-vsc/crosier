import subprocess
from unittest.mock import MagicMock, patch

from crosier.verdict import build_verdict_prompt, generate_verdict, parse_verdict


def test_build_verdict_prompt_references_stdin():
    prompt = build_verdict_prompt()
    assert "stdin" in prompt
    assert "proceed" in prompt
    assert "flag" in prompt


def test_parse_verdict_valid_proceed():
    raw = '{"status": "proceed", "confidence": "high", "flagged_claim": null, "reason": null, "suggested_check": null}'
    result = parse_verdict(raw)
    assert result["status"] == "proceed"
    assert result["confidence"] == "high"


def test_parse_verdict_valid_flag():
    raw = '{"status": "flag", "confidence": "medium", "flagged_claim": "the API returns sorted results", "reason": "no test confirms this", "suggested_check": "check the API docs"}'
    result = parse_verdict(raw)
    assert result["status"] == "flag"
    assert result["flagged_claim"] == "the API returns sorted results"


def test_parse_verdict_strips_markdown_fences():
    raw = '```json\n{"status": "proceed", "confidence": "low", "flagged_claim": null, "reason": null, "suggested_check": null}\n```'
    result = parse_verdict(raw)
    assert result["status"] == "proceed"


def test_parse_verdict_fills_missing_optional_keys_with_none():
    raw = '{"status": "proceed", "confidence": "low"}'
    result = parse_verdict(raw)
    assert result["flagged_claim"] is None
    assert result["reason"] is None
    assert result["suggested_check"] is None


def test_parse_verdict_returns_none_on_invalid_json():
    assert parse_verdict("not json at all") is None


def test_parse_verdict_returns_none_on_invalid_status():
    raw = '{"status": "maybe", "confidence": "low"}'
    assert parse_verdict(raw) is None


def test_parse_verdict_returns_none_on_invalid_confidence():
    raw = '{"status": "proceed", "confidence": "extremely"}'
    assert parse_verdict(raw) is None


def test_parse_verdict_recovers_json_from_surrounding_prose():
    raw = (
        "Sure, here is my assessment:\n"
        '{"status": "flag", "confidence": "medium", "flagged_claim": "x", '
        '"reason": "y", "suggested_check": "z"}\n'
        "Let me know if you need anything else!"
    )
    result = parse_verdict(raw)
    assert result["status"] == "flag"
    assert result["flagged_claim"] == "x"


@patch("crosier.verdict.subprocess.run")
def test_generate_verdict_returns_parsed_dict_on_success(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"status": "proceed", "confidence": "high", "flagged_claim": null, "reason": null, "suggested_check": null}',
    )
    result = generate_verdict("a digest", model="sonnet")
    assert result["status"] == "proceed"
    kwargs = mock_run.call_args.kwargs
    args = mock_run.call_args.args[0]
    assert kwargs["input"] == "a digest"
    assert "a digest" not in args


@patch("crosier.verdict.subprocess.run")
def test_generate_verdict_returns_none_on_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert generate_verdict("a digest", model="sonnet") is None


@patch("crosier.verdict.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=45))
def test_generate_verdict_returns_none_on_timeout(mock_run):
    assert generate_verdict("a digest", model="sonnet") is None
