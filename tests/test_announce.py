# tests/test_announce.py
"""What the Stop hook prints. A flag holds the turn through top-level
`decision: block` and hands its reason to the agent; `systemMessage` reaches
the user's screen and never the agent."""

from crosier.announce import GATE_SUFFIX, activation_text, gate_output, merge_system_message

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


def test_flag_blocks_the_stop_with_the_whole_concern_in_the_reason():
    out = gate_output(FLAG, "medium")
    assert out["decision"] == "block"
    assert "hookSpecificOutput" not in out
    text = out["reason"]
    assert "the endpoint is idempotent so retries are safe" in text
    assert "no test confirms this" in text
    assert "run the retry test twice" in text
    assert "unverified_claim" in text
    assert text.endswith(GATE_SUFFIX)
    assert "the endpoint is idempotent" in out["systemMessage"]


def test_gate_suffix_lets_the_agent_keep_a_correct_answer():
    assert "restate the answer unchanged" in GATE_SUFFIX
    assert "Do not reply to this note or justify earlier turns." in GATE_SUFFIX


def test_proceed_and_none_let_the_answer_stand():
    assert gate_output(PROCEED, "low") is None
    assert gate_output(None, "low") is None


def test_low_confidence_flag_does_not_block():
    # A guess from a blind reviewer is not worth a held turn.
    assert gate_output({**FLAG, "confidence": "low"}, "medium") is None


def test_flag_without_claim_uses_generic_fallback():
    verdict = {**FLAG, "flagged_claim": None, "reason": None, "suggested_check": None, "evidence": None}
    assert "an assumption in the recent work" in gate_output(verdict, "low")["reason"]


def test_activation_line_shares_the_user_slot_with_a_block():
    out = merge_system_message(gate_output(FLAG, "medium"), activation_text())
    assert out["decision"] == "block"
    assert out["systemMessage"].startswith("Crosier active")
    assert "the endpoint is idempotent" in out["systemMessage"]
