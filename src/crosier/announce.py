"""Turn a verdict into the one line of text the main session will state.
This is delivered via hook stdout injection, so the wording here is what
the user actually sees."""


def format_announcement(verdict: dict | None, turn_number: int, announce_mode: str) -> str | None:
    if verdict is None:
        return None
    if verdict["status"] == "proceed":
        if announce_mode == "on-flag":
            return None
        return f"Direction check (turn {turn_number}): no issues found."

    claim = verdict.get("flagged_claim") or "an assumption in the recent work"
    reason = verdict.get("reason")
    message = f"Direction check flagged a possible issue: {claim} — recommend confirming before I continue."
    if reason:
        message += f" ({reason})"
    return message


def format_backoff_notice() -> str:
    return "Direction check disabled for this session after repeated errors — see .crosier/errors.log."
