"""Get an independent verdict from a fresh, zero-context `claude -p` call
that sees only the digest — never the transcript, never the main session's
history. This is the actual "fresh eyes" step."""

import json
import subprocess

VERDICT_PROMPT_TEMPLATE = """You are an independent reviewer with no memory of this session beyond the digest below. Decide whether the work described still looks on track, or whether something looks like it has drifted from a sound premise. Respond with ONLY a JSON object, nothing else, no markdown fences, in exactly this shape:

{{"status": "proceed" or "flag", "confidence": "low" or "medium" or "high", "flagged_claim": string or null, "reason": string or null, "suggested_check": string or null}}

"proceed" is a normal, common, expected result — only use "flag" for a real, specific concern, never to justify your own existence.

Digest:
{digest}
"""

VALID_STATUS = {"proceed", "flag"}
VALID_CONFIDENCE = {"low", "medium", "high"}
OPTIONAL_KEYS = ("flagged_claim", "reason", "suggested_check")


def build_verdict_prompt(digest: str) -> str:
    return VERDICT_PROMPT_TEMPLATE.format(digest=digest)


def parse_verdict(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") not in VALID_STATUS:
        return None
    if data.get("confidence") not in VALID_CONFIDENCE:
        return None
    for key in OPTIONAL_KEYS:
        data.setdefault(key, None)
    return data


def generate_verdict(digest: str, model: str = "sonnet", timeout: int = 45) -> dict | None:
    prompt = build_verdict_prompt(digest)
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return parse_verdict(result.stdout or "")
