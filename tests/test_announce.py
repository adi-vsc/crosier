# tests/test_announce.py
from crosier.announce import format_announcement, format_backoff_notice


def test_proceed_always_mode_prints_clean_pass():
    verdict = {"status": "proceed", "confidence": "high", "flagged_claim": None, "reason": None, "suggested_check": None}
    result = format_announcement(verdict, turn_number=24, announce_mode="always")
    assert result == "Direction check (turn 24): no issues found."


def test_proceed_on_flag_mode_is_silent():
    verdict = {"status": "proceed", "confidence": "high", "flagged_claim": None, "reason": None, "suggested_check": None}
    result = format_announcement(verdict, turn_number=24, announce_mode="on-flag")
    assert result is None


def test_flag_always_mode_includes_claim_and_reason():
    verdict = {
        "status": "flag",
        "confidence": "medium",
        "flagged_claim": "the endpoint is idempotent",
        "reason": "no test confirms this",
        "suggested_check": None,
    }
    result = format_announcement(verdict, turn_number=30, announce_mode="always")
    assert "the endpoint is idempotent" in result
    assert "no test confirms this" in result


def test_flag_on_flag_mode_still_announces():
    verdict = {"status": "flag", "confidence": "low", "flagged_claim": "X", "reason": None, "suggested_check": None}
    result = format_announcement(verdict, turn_number=30, announce_mode="on-flag")
    assert result is not None
    assert "X" in result


def test_none_verdict_produces_no_announcement():
    assert format_announcement(None, turn_number=10, announce_mode="always") is None


def test_flag_without_claim_uses_generic_fallback():
    verdict = {"status": "flag", "confidence": "low", "flagged_claim": None, "reason": None, "suggested_check": None}
    result = format_announcement(verdict, turn_number=1, announce_mode="always")
    assert "an assumption in the recent work" in result


def test_backoff_notice_mentions_error_log():
    assert ".crosier/errors.log" in format_backoff_notice()
