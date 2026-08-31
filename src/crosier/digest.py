"""Compress a session excerpt into a short digest via a headless `claude -p`
call. This is the token-efficiency lever: everything downstream (the verdict
call) reads this digest, never the raw transcript."""

import subprocess

DIGEST_PROMPT_TEMPLATE = """You are compressing a slice of an AI coding session for a fresh reviewer who will never see the full transcript. Read the session excerpt below and produce ONLY the following markdown, nothing else:

## Current task
<one paragraph>

## Decisions made since last check
- <decision> (source: self | user)

## Assumptions currently being built on
- <most load-bearing, least defensible assumption>
- <second least defensible assumption>

## User corrections/redirections since last check
- <any time the user corrected or redirected the agent, or "none">

## Tool actions taken (compressed)
- <summary, not a full log>

Keep the whole output under 2000 tokens. Session excerpt:

{excerpt}
"""


def build_digest_prompt(excerpt: str) -> str:
    return DIGEST_PROMPT_TEMPLATE.format(excerpt=excerpt)


def generate_digest(excerpt: str, model: str = "sonnet", timeout: int = 45) -> str | None:
    prompt = build_digest_prompt(excerpt)
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
    output = (result.stdout or "").strip()
    return output or None
