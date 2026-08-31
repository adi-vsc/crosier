# Crosier — design spec

Date: 2026-08-31
Status: approved for implementation

## 1. Problem

LLM agent sessions degrade in quality as they get longer — this is measured, not anecdotal (see §2). Claude Code has no built-in mechanism to catch this from the inside: the model that's drifting is the same model deciding whether it's drifted, and by definition it can't reliably see its own blind spot. Existing tools either require a manual invocation per check (fresheyes — "review this with fresh eyes," on-demand, code-diff scoped only) or are unshipped research (Cognitive Companion, continuous parallel monitoring, ~11% token overhead, not Claude-specific).

Crosier is an automatic, install-and-forget Claude Code plugin that periodically hands a compressed digest of the session to a fresh, zero-context Claude instance for a second opinion on whether the main session is still on track — general direction/premise drift, not just code correctness — at a fraction of the token cost of continuous monitoring.

## 2. Evidence base

| Finding | Source |
|---|---|
| 18 frontier models show monotonically decreasing accuracy as input length grows, even well below their context limit | [Chroma, "Context Rot" (Jul 2025)](https://www.trychroma.com/research/context-rot) |
| Every top model tested drops 39% average accuracy in multi-turn vs. single-turn on the same task | [arXiv:2505.06120](https://arxiv.org/abs/2505.06120) |
| Sequential multi-turn presentation cuts accuracy/abstention ~30% average, up to 65% in some models, across 17 LLMs | [arXiv:2603.11394](https://arxiv.org/abs/2603.11394) |
| Reasoning degradation / looping / drift / stuck states occur at rates up to 30% on hard multi-step tasks; a parallel LLM monitor cuts repetitive looping 52-62% at ~11% token overhead | [arXiv:2604.13759](https://arxiv.org/abs/2604.13759) — the overhead number Crosier's periodic (not continuous) design is built to beat |
| Sub-agent isolation + condensed 1,000-2,000 token summaries is Anthropic's own recommended context-engineering pattern | [Anthropic, "Effective context engineering for AI agents"](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |

## 3. Non-goals (v1)

- Not a code reviewer (fresheyes already does that well — Crosier is complementary, not a replacement).
- Not a continuous/parallel monitor (that's the Cognitive Companion design point, and its overhead cost is exactly what periodic checking avoids).
- No bundled analytics/telemetry service — everything runs locally against the user's own `claude` CLI auth.
- No custom model-hosting — Crosier never talks to a raw API; it always shells out to the user's already-authenticated `claude` CLI.

## 4. Architecture

Four components, three of which spend zero LLM tokens:

```
UserPromptSubmit (every turn)          PreCompact (Claude Code's own trigger)
        |                                        |
   heuristic scorer                        (always escalates,
   (pure Python, no                         bypasses heuristic)
    network call)                                 |
        |                                         |
        +-------------- score >= threshold? ------+
                              |
                          escalate
                              |
                 ---------------------------
                 |                         |
          Call 1: digest              (headless `claude -p`,
          (Sonnet, transcript          zero shared context
           delta since last            with main session)
           check -> ~1-2K token
           digest)
                 |
          Call 2: verdict
          (fresh Sonnet, digest
           only as input -> JSON
           proceed/flag verdict)
                 |
          hook stdout injected as context
          (same mechanism as any
           UserPromptSubmit/SessionStart
           hook — see the CAVEMAN MODE
           injection visible in this
           very transcript as a working
           real-world example)
                 |
          main session states the
          verdict on its next turn,
          no custom announcement
          code needed in the model
```

### 4.1 Heuristic scorer (`src/crosier/heuristic.py`)

Pure function, deterministic, unit-testable without any LLM or network access. Tracks three signals since the last check, reset on every escalation:

- `turns_since_check` — incremented per `UserPromptSubmit` fire.
- `tokens_since_check` — estimated from transcript char count / 4 (good enough; this gates a decision, it doesn't need to be exact).
- `tool_repetition_rate` — of the last 10 tool calls, the fraction that are the "same tool + near-identical args" as another call in that window (catches the loop symptom from the Cognitive Companion paper).

v1 rule (deliberately simple — a weighted-max-of-independent-triggers, not a trained model; this is the thing to tune with real usage data post-launch, not to over-engineer now):

```
escalate = (turns_since_check >= 20)
        or (tokens_since_check >= 60_000)
        or (tool_repetition_rate >= 0.4)
```

`PreCompact` always escalates regardless of this rule — it is Claude Code's own signal that the transcript has become too large, which is definitionally the highest-drift-risk moment in the session.

All three thresholds are config-overridable (§4.4) but ship with defaults — no config file is required to get correct default behavior.

### 4.2 Digest generation (`src/crosier/digest.py`)

On escalation, shells out to `claude -p --model sonnet` with a fixed prompt template, given only the transcript **delta since the last check** (not the whole session — keeps this call, and its cost, bounded regardless of total session length). Output is a structured digest, capped at ~2,000 tokens:

```markdown
## Current task
<one paragraph>

## Decisions made since last check
- <decision> (source: self | user)
...

## Assumptions currently being built on
- <assumption 1> (least defensible)
- <assumption 2> (second least defensible)

## User corrections/redirections since last check
- <any time the user had to correct or redirect — a real-time drift signal>

## Tool actions taken (compressed)
- <summary, not full log>
```

The "least defensible assumptions" framing is deliberate — it mirrors the anti-drift ledger pattern (tag provenance, name the two least defensible, re-check them) rather than inventing a new one.

### 4.3 Verdict (`src/crosier/verdict.py`)

Second, independent `claude -p --model sonnet` call. Input: the digest only — no transcript, no session history, no shared context with the main session. This is the actual "fresh eyes": a Claude instance that has never seen the session, forming an opinion from the compressed digest alone.

Output: strict JSON —

```json
{
  "status": "proceed" | "flag",
  "confidence": "low" | "medium" | "high",
  "flagged_claim": "string or null",
  "reason": "string or null",
  "suggested_check": "string or null"
}
```

`status: "proceed"` is a valid, expected, common outcome — the null exit stays live. This is not a tool that must find something every time to justify its existence.

### 4.4 Delivery & config

The hook's stdout is injected as context on the next turn — identical mechanism to any other Claude Code hook (verified working in-session: this conversation's own "CAVEMAN MODE ACTIVE" line is a `UserPromptSubmit` hook injection). No custom rendering code is needed in Crosier or in Claude Code — the main model reads the injected verdict like any other context and states it naturally.

Default announcement (verified requirement: **on by default, no setup step to enable it** — install-and-forget, opt-out only, never opt-in):

- Clean pass: `Direction check (turn 24): no issues found.`
- Flagged: `Direction check flagged a possible issue: <claim> — recommend confirming before I continue.`

Config file: `.crosier.toml` in project root, entirely optional — every field has a shipped default:

```toml
[crosier]
enabled = true
announce = "always"        # "always" | "on-flag"
digest_model = "sonnet"
verdict_model = "sonnet"
turn_threshold = 20
token_threshold = 60000
repetition_threshold = 0.4
```

### 4.5 Failure handling

`claude -p` shellouts can fail (rate limit, auth expired, offline, CLI not on PATH). Crosier must **fail open, never block the main session**:

- Any failure in the digest/verdict path → hook exits 0 with no injected content (silent skip, not a crash, not a blocked turn).
- After 2 consecutive failures, back off — disable escalation for the rest of the session (avoid retry-storming a broken auth/network state) and inject one quiet notice: `Direction check disabled for this session after repeated errors — see .crosier/errors.log.`
- Never surface a raw exception or stack trace into the main session's context.

## 5. Packaging

- `pip install crosier` — pyproject.toml, matches the driftwatch/snapdrift precedent.
- Ships as a proper Claude Code plugin: hook scripts + a manifest that self-registers `UserPromptSubmit` and `PreCompact` in the user's `settings.json` on install (no manual hook-wiring step — that would violate install-and-forget).
- MIT license, README with the evidence table from §2, CHANGELOG.md, LICENSE — matching the quality bar already set by driftwatch and morphos.

## 6. Testing

- `heuristic.py` — pure function, fully unit-tested with synthetic turn/token/repetition sequences, no network.
- `digest.py` / `verdict.py` — integration tests mock the `claude -p` subprocess call (no real API spend in CI); a small number of real-call smoke tests gated behind an opt-in env var for local/manual runs.
- End-to-end: a scripted fake "long session" transcript fixture that should trigger escalation at the documented thresholds, verifying the heuristic fires exactly when it should (no false negatives on the documented triggers, no obviously-wrong false positives on a short/clean fixture).

## 7. Statistics discipline

Do not publish an effectiveness number (e.g. "catches X% of drift") until it's actually measured against a real on/off benchmark (methodology: run N long agentic sessions with Crosier on vs. off, measure caught-drift rate, false-positive rate, measured token overhead, downstream task success delta). The README ships with the cited literature in §2 and is explicit that Crosier's own effectiveness numbers are pending a v1.x benchmark release, not asserted at launch.
