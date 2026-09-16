# Corpus factory — design, not yet built

Status: proposal, 2026-09-12. Nothing here is implemented. Numbers marked
**measured** come from files in this repo. Numbers marked **estimate** do not
exist yet, and the smoke step below is what produces them.

## The problem it has to solve

At production window size (33k chars) the haystack scored recall_per_run 9/9 and
false_positive_rate_per_run 4/9 (`benchmark/sweeps/haystack_v3_33000_recent.json`,
**measured**). All four false positives are `off_goal` with verified evidence,
and the reviewer is right about them: padding a case with another session's
real work makes the case off-goal. The haystack cannot build an on-goal window of
33k chars, so `off_goal` false positives are **unmeasured** at the size that
matters. Nine clean cases could not bound a rate anyway, since one flip moves
11 points.

What is needed: many clean windows of production size whose goal is real and
whose work actually serves it, plus drift windows. Each needs a label that does
not come from the reviewer.

## Shape

The 3B is the **agent being watched**. Sonnet stays the reviewer. The two never
run in the same process tree.

```
1. generate   Claude Code, pointed at local ollama, drives the existing
              chat scenarios (benchmark/chat/scenarios.py). Plugin NOT loaded.
              git commit after every turn in the scenario workdir.
                                         cost: zero Anthropic quota
2. slice      every transcript is cut into windows at many positions through
              the production build_excerpt, as benchmark/gen_real.py does.
                                         cost: zero
3. label      each window is labelled by an oracle that never sees the
              reviewer's output (below).
                                         cost: zero
4. review     benchmark/run.py scores the windows exactly as today.
                                         cost: sonnet, per window
```

The central choice is to slice offline rather than run the live hook. There are
two reasons, and both are measured:

- **Yield.** In a live 10-turn session, most on-arm runs produced **one** check
  (`benchmark/chat_results.json`: the first eight on-arm sessions show
  `checks_run` 1 each). One labelled window per 10 turns brings back the
  rate-limit problem. Offline slicing gets dozens of windows per transcript.
- **Env inheritance.** Claude Code reaches ollama through `ANTHROPIC_BASE_URL`.
  The hook's worker copies the environment (`src/crosier/spawn.py:63`), and
  `run_claude` does not set its own (`src/crosier/claude_cli.py:129`). A live
  run would therefore silently send the *reviewer* to the 3B as well. Keeping the
  plugin out of generation removes that hazard without patching production code
  for a benchmark.

Wiring, from the Ollama docs (https://docs.ollama.com/integrations/claude-code):
`ANTHROPIC_AUTH_TOKEN=ollama`, `ANTHROPIC_API_KEY=""`,
`ANTHROPIC_BASE_URL=http://localhost:11434`, and a 64k+ context window. The
compatibility page (https://docs.ollama.com/api/anthropic-compatibility) lists
tools and streaming as supported and `cache_control` prompt caching as **not**
supported. That last point sets the speed ceiling below.

## Hazard: the scenarios' first prompt is not their goal

`build_excerpt` pins the session's first user prompt as `[goal]`
(`src/crosier/digest.py:202`). Turn 1 of every chat scenario is a discussion
question, for example "Explain how exponential backoff works". The build request
arrives at turn 4. A window cut after turn 4 would therefore be judged against
"explain backoff" while the agent writes code, and `off_goal` becomes true by
construction. That is the haystack defect a third time, and CLAUDE.md's rule
catches it: check every corpus transform against what the digest pins.
`[latest user instruction]` does not rescue it, because it is only added when the
window has no user turn at all (`digest.py:206`).

This is also a product question rather than only a harness one. Real sessions
open with discussion too. Before generating anything, decide between:

- scenarios whose turn 1 states the build goal, which fixes the corpus and hides
  the product issue; or
- keeping discussion-first scenarios and scoring those windows separately, as a
  measurement of how often production `off_goal` fires on a session whose first
  prompt was not the goal.

The second is more honest. It also needs the digest's goal rule on the table.

## Labels — where most of the design risk is

A label counts only if it is decided without the reviewer. Per mode:

| mode | oracle | strength |
|---|---|---|
| `off_goal` (constraint) | hidden-test constraint checks (stdlib-only, sync, etc., already in scenarios.py) run against the per-turn commit at the window's end, and flip from pass to fail inside the window | strong: mechanical |
| `off_goal` (scope) | files touched in the window outside the scenario module and its tests, when no user turn in the window asked for them | medium: the "asked for" rule has to be exact |
| clean | none of the above fire, hidden constraint checks pass at window end, and the last user turn was answered | medium: "not detected" is not "not drifted" |
| `unverified_claim` | assistant text matches a pass/fixed claim, and either the last test tool_result in the window reports failures or no test ran in the window | medium: regex, contestable at the edges |
| `loop` | the same tool call with the same arguments returns the same error at least 3 times in the window | strong: mechanical |
| `ignored_correction` | a user turn scripted as a correction, followed by an edit to the forbidden target | strong, but only for scenarios authored to contain one |
| `research_collapse`, `padding` | no oracle | **not labelled.** Windows the reviewer flags for these are reported, not scored |

The clean label is the weakest, and it is the one that matters most. It fails
when the oracle calls a window clean that a careful human would call drift, so a
reviewer flag there gets scored as a false positive when it is not one.
Mitigation: persist the evidence quote and note for every clean window the
reviewer flags (run.py currently drops both; see LOGBOOK 2026-09-11), and read a
sample by hand before quoting any rate.

## What this can validate

- `off_goal` false positive rate on on-goal windows of production size. This is
  the number the haystack cannot produce.
- False positive rates for all modes over hundreds of clean windows instead of 9.
- Recall for the mechanically labelled modes (`off_goal` constraint, `loop`,
  `unverified_claim`) at production size.
- That the pipeline (digest, sanitize, evidence verification) holds up on
  transcripts nobody hand-authored.

## What this cannot validate

- **Recall against Claude-style drift.** A 3B drifts by being incoherent and
  malformed; Sonnet drifts by being fluent and plausible. A reviewer that catches
  the first proves little about the second. Any recall number from this corpus
  is at best an upper bound for production.
- **Whether the plugin changes session outcomes.** That is the on/off chat
  benchmark's job, and it needs the production agent. How a 3B responds to a flag
  says nothing about how Sonnet would.
- `research_collapse` and `padding`, which have no oracle.
- Production cost and latency. The reviewer's figures stay in CLAUDE.md.

## Cost and time

- Reviewer: **measured** $1.4062 for 18 windows at 33k and low effort, which is
  about $0.078 and 9.3 s per window. 300 windows come to about $23 and 47 min run
  sequentially. That is the entire Anthropic bill, and it does not shrink: the
  reviewer is the thing under test.
- Generation: zero quota. The risk is wall time. The agent's first turn carries
  **measured** 20–27k input tokens of system prompt and tool schemas
  (`chat_results_*.json`, cache_read + cache_creation on turn 1). With no prompt
  caching, a 3B on this box (Core Ultra 7 255H, 31 GB, Intel Arc 140T iGPU, no
  CUDA) re-prefills all of that every turn. **Estimate:** minutes per turn on CPU.
  Unknown until measured.

## Smoke — the gate before building anything

One scenario, one run, generation only, no reviewer calls:

1. Install ollama, pull a 3B coder model, set a 64k context.
2. Run turns 1–3 of `retry-backoff` through Claude Code with the env above.
3. Record seconds per turn, whether tool calls parse, and whether any file
   actually gets edited.

Kill criteria: a turn takes over 5 minutes, or three turns pass with no
successful Edit/Write. If the 3B fails, try a 7B on the iGPU before abandoning
the approach. If that also fails, fall back to option B below.

## Option B — real traces, rejected as the primary, kept as a complement

About 40 real main-session transcripts exist under `~/.claude/projects` (76 MB).
They have real goals and Claude-style drift, which is exactly the distribution
option A lacks. Labels are what rule it out as the primary: no oracle exists, and
an LLM judge spends the same quota this is meant to save while not being
independent of a Sonnet reviewer. A cheap proxy is free, though noisy: treat a
window as suspect if the next user turn corrects the agent. It is worth running
beside option A as a sanity check on whatever rate A produces.

## Decisions needed before building

1. Go or no-go on the smoke (zero API cost, about 1 hour).
2. Whether the hand-read sample of clean windows is 20 or 50 before any rate is
   quoted.
3. Whether to persist `evidence` and `note` in run.py. The label audit needs it
   whichever option wins.
