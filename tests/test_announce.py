# tests/test_announce.py
"""What the hook prints. Two channels, deliberately separate: `additionalContext`
reaches the agent as a system reminder at the point the hook fired, and is
used only for a real flag; `systemMessage` reaches the user's screen and never
the agent, so a clean verdict can be shown without spending a line of the
agent's attention on it. At Stop, any `additionalContext` continues the
conversation, which is exactly right for a flag and exactly wrong for
"no issues found"."""

from crosier.announce import ADVISORY_SUFFIX, backoff_output, hook_output, stale_output

PROCEED = {"status": "proceed", "confidence": "high", "category": None, "flagged_claim": None, "reason": None, "suggested_check": None, "evidence": None}
FLAG = {
    "status": "flag",
    "confidence": "medium",
    "category": "unverified_claim",
    "flagged_claim": "the endpoint is idempotent",
    "reason": "no test confirms this",
    "suggested_check": "run the retry test twice",
    "evidence": "[assistant] the endpoint is idempotent so retries are safe",
    "evidence_verified": True,
}


def _context(out):
    return out["hookSpecificOutput"]["additionalContext"]


def test_flag_reaches_the_agent_as_additional_context_for_the_firing_event():
    out = hook_output("PostToolBatch", FLAG, turn_number=30, announce_mode="always")
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolBatch"
    text = _context(out)
    assert "the endpoint is idempotent" in text
    assert "no test confirms this" in text
    assert "run the retry test twice" in text
    assert "unverified_claim" in text
    assert text.endswith(ADVISORY_SUFFIX)


def test_flag_quotes_its_evidence_so_the_agent_can_find_the_line():
    out = hook_output("UserPromptSubmit", FLAG, turn_number=30, announce_mode="always")
    assert "the endpoint is idempotent so retries are safe" in _context(out)


def test_flag_also_tells_the_user_on_screen():
    out = hook_output("PostToolBatch", FLAG, turn_number=30, announce_mode="on-flag")
    assert "the endpoint is idempotent" in out["systemMessage"]


def test_advisory_suffix_forbids_arguing_and_abandoning():
    assert "do not justify earlier turns" in ADVISORY_SUFFIX
    assert "Finish the step in progress first." in ADVISORY_SUFFIX
    assert "do not reply to this note" in ADVISORY_SUFFIX


def test_flag_at_stop_continues_the_turn_through_additional_context():
    out = hook_output("Stop", FLAG, turn_number=30, announce_mode="always")
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "the endpoint is idempotent" in _context(out)
    assert "decision" not in out


def test_proceed_is_shown_to_the_user_only():
    out = hook_output("PostToolBatch", PROCEED, turn_number=24, announce_mode="always")
    assert "hookSpecificOutput" not in out
    assert "no issues" in out["systemMessage"]
    assert "turn 24" in out["systemMessage"]


def test_proceed_on_flag_mode_is_silent():
    assert hook_output("PostToolBatch", PROCEED, turn_number=24, announce_mode="on-flag") is None


def test_proceed_at_stop_never_emits_additional_context():
    # At Stop any additionalContext makes Claude continue the turn. A clean
    # bill of health must not do that.
    out = hook_output("Stop", PROCEED, turn_number=24, announce_mode="always")
    assert out is None or "hookSpecificOutput" not in out


def test_low_confidence_flag_does_not_reach_the_agent():
    # A guess from a blind reviewer is not worth a train of thought.
    low = {**FLAG, "confidence": "low"}
    out = hook_output("PostToolBatch", low, turn_number=3, announce_mode="always", min_flag_confidence="medium")
    assert "hookSpecificOutput" not in out
    assert "nothing worth interrupting" in out["systemMessage"]
    assert hook_output("PostToolBatch", low, turn_number=3, announce_mode="on-flag", min_flag_confidence="medium") is None


def test_flag_without_claim_uses_generic_fallback():
    verdict = {**FLAG, "flagged_claim": None, "reason": None, "suggested_check": None, "evidence": None}
    out = hook_output("PostToolBatch", verdict, turn_number=1, announce_mode="always", min_flag_confidence="low")
    assert "an assumption in the recent work" in _context(out)


def test_none_verdict_produces_no_output():
    assert hook_output("PostToolBatch", None, turn_number=10, announce_mode="always") is None


def test_stale_notice_is_user_only_and_respects_the_mode():
    assert "stale" in stale_output("always")["systemMessage"]
    assert stale_output("on-flag") is None


def test_backoff_notice_names_the_log():
    out = backoff_output("C:/x/errors.log")
    assert "C:/x/errors.log" in out["systemMessage"]
    assert "hookSpecificOutput" not in out
