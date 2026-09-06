"""Treat transcript content as hostile input.

Anything the main agent read lands in the transcript verbatim — a tainted
package.json, a downloaded script, a web page. Crosier then forwards that
text to two fresh models that have no way to tell agent reasoning from file
contents. So the excerpt is fenced and de-fanged on the way in, and whatever
the verdict model puts in a free-text field is scrubbed on the way out,
because that field is printed straight into the main session's context.
"""

import re

EXCERPT_OPEN = "<<<UNTRUSTED_TRANSCRIPT"
EXCERPT_CLOSE = "UNTRUSTED_TRANSCRIPT>>>"

FIELD_CHAR_CAP = 240

# Tags Claude Code (or a model reading this text) may treat as structural
# rather than as content. Flattened to an inert label.
_TAG_RE = re.compile(
    r"</?\s*(system-reminder|system|human|assistant|user|instructions?|important)\b[^>]*>",
    re.IGNORECASE,
)

# Phrases whose only purpose is to retarget the reader. The excerpt is being
# summarised, not executed, so replacing the phrase costs nothing real and
# leaves a visible marker the digest is told to report.
_OVERRIDE_RES = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+\w*\s*instructions?", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+\w*\s*instructions?", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\b", re.I),
    re.compile(r"new\s+(system\s+)?(instructions?|prompt|rules?)\s*:", re.I),
    re.compile(r"(^|\n)\s*#{0,3}\s*system\s*(prompt|message)\s*:", re.I),
)

_MARKER = "[redacted-injection-phrase]"


def sanitize_excerpt(excerpt: str) -> str:
    """Neutralize the excerpt and wrap it in markers the prompts refer to."""
    text = excerpt or ""
    # The content must not be able to close its own fence.
    text = text.replace(EXCERPT_OPEN, "[marker]").replace(EXCERPT_CLOSE, "[marker]")
    text = _TAG_RE.sub(lambda m: f"[tag:{m.group(1).lower()}]", text)
    for pattern in _OVERRIDE_RES:
        text = pattern.sub(_MARKER, text)
    return f"{EXCERPT_OPEN}\n{text}\n{EXCERPT_CLOSE}"


def sanitize_field(value, cap: int = FIELD_CHAR_CAP) -> str | None:
    """Flatten one free-text verdict field before it reaches the main session.

    A flagged claim is quoted back to the main agent, so it is an injection
    path in its own right: a compromised excerpt that survives the digest
    could otherwise emit a multi-line instruction block through this field.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    text = _TAG_RE.sub(lambda m: f"[tag:{m.group(1).lower()}]", text)
    for pattern in _OVERRIDE_RES:
        text = pattern.sub(_MARKER, text)
    text = text.replace("`", "'")
    if not text:
        return None
    if len(text) > cap:
        text = text[: cap - 1].rstrip() + "…"
    return text
