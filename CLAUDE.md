# Crosier — working instructions

Crosier is a Claude Code plugin that watches a session for drift and interrupts
with a second opinion from a reviewer that has never seen the session. Read
`CHANGELOG.md` for what shipped and `scope.md` (gitignored, local) for open
questions and decisions.

## What Crosier is for — read this first

The target behaviour already happens by hand in the sessions that build
Crosier. After enough turns the agent loses accuracy and efficiency: it builds
on its own earlier output as if that were settled. The user then says "spawn a
Sonnet subagent to attack this". The subagent gets the decision and its evidence,
with no history. It finds what is wrong, and the agent gets back on track.

Crosier exists to produce that effect without the user asking, and to do it:

- **Before the answer is delivered, not after.** Today the bad message ships,
  the user reads it, and only then asks for a second look. Detection after
  delivery is the failure being replaced. The Stop gate is Crosier's only
  review; a PreToolUse gate is the next candidate.
- **With far fewer tokens.** Current cost is in "What a check costs" below. A
  check that fires on every turn, or reads mostly tool traffic, is the problem
  being solved, not an acceptable baseline.
- **Only when it matters.** A second look on a good answer costs tokens and can
  talk the agent out of a correct result. False blocks count against Crosier.

Measure it on real multi-turn sessions (`benchmark/chat`, v2): bad drafts
intercepted before delivery, false blocks, reviewer tokens, added latency. Not
detection accuracy on labelled excerpts.

Not settled: what the manual loop's reviewer actually reads is a short brief of
the decision with locators, written by the agent under review, not a transcript
excerpt. Whether a brief can replace the excerpt is open in `scope.md`. The
author of a brief can leave out exactly the thing that is wrong.

## Log every major change

`LOGBOOK.md` (gitignored, local) is the project's step-by-step record: what was
done, why, what it measured, and what was rejected. Append to it — never
rewrite or compress a past entry.

Append an entry when any of these happen:

- a commit lands
- a benchmark run finishes (record the numbers, not "it improved")
- a design decision is made, including a rejection and its reason
- an approach is abandoned, with what killed it
- a measurement changes what we believe

Entry shape:

```
### YYYY-MM-DD — short title
**What:** the change, one or two lines.
**Why:** the measurement or argument that forced it.
**Numbers:** figures with their denominator and run, or "none".
**Rejected:** what was considered and dropped, with the reason. Or "none".
**Files:** paths touched.
```

The three records have different jobs and do not substitute for each other:
`CHANGELOG.md` is release-facing and committed, `scope.md` holds live open
questions, `LOGBOOK.md` is the append-only history including dead ends.

## Check every big decision with `crosier-cold-check`

Before acting on a big decision, spawn `crosier-cold-check` and give it the
decision alone: the options, the choice, the reasoning, and the evidence with
locators. No conversation history, no "we established". It attacks the choice
and returns what breaks. Act on what survives, and record the kill list in the
decision's `LOGBOOK.md` entry.

A big decision is one that is expensive to undo or that changes what a number
means:
- architecture or hook behaviour (what blocks, what runs synchronously, what the
  reviewer sees)
- a benchmark's design: arms, scenarios, oracles, scoring rules, metrics
- anything that spends real money or hours (a paid run, its size)
- abandoning or rejecting an approach
- changing a default that ships

Not big: routine edits, test additions, bug fixes with an obvious cause, status
reports.

## How big changes are executed

For any big change (same definition as above), in this order:

1. **Stress test the plan first.** The plan, including the agent's own proposals,
   goes to `crosier-cold-check` as a quoted artifact with locators and no
   history. The plan is rewritten around what survives before any code is
   written.
2. **Split into workstreams.** Independent workstreams run in parallel as
   separate agents where they do not share files (`isolation: "worktree"` when
   they could collide).
3. **Engineers are specialist agents; the brain is Opus.** Coding, corpus
   building and measurement go to the matching agent below. Opus (the main
   session) only plans, integrates, and judges results.
4. **Review before merge.** A diff of 100+ changed lines, or any change to hook
   behaviour, reviewer isolation, `sanitize.py` or benchmark scoring, gets
   `crosier-diff-review`. A smaller diff is read by the main session itself. The
   suite passes under `py -3 -m pytest` before anything lands.

## Subagents: use the specialist, never `general-purpose`

Specialist agents live in `.claude/agents/` (local, `.claude/` is gitignored).
Each has only the tools its job needs, skips CLAUDE.md (`omitClaudeMd`) and
carries the rules it needs inline, caps its turns and its report length.
Measured 2026-09-17: `crosier-locator` starts at 3.9k tokens of context where
`general-purpose` starts at 21.7k headless and 47k in an interactive session,
and every request re-reads that baseline.

| Task | Agent | Model |
|---|---|---|
| Attack a big decision or plan | `crosier-cold-check` | sonnet |
| Review a large or sensitive diff | `crosier-diff-review` | sonnet |
| Label a prepared batch file (census, confirmation) | `crosier-batch-labeller` | sonnet |
| Blind hindsight label of replayed Stop-gate turns | `crosier-hindsight-labeller` | opus |
| Match a reviewer flag to a labelled problem | `crosier-flag-matcher` | opus |
| Build or change `benchmark/**` code | `crosier-bench-engineer` | sonnet |
| Change `src/crosier/**` or `hooks/**` | `crosier-src-engineer` | sonnet |
| Add tests for existing behaviour | `crosier-test-writer` | sonnet |
| Remove dead code or retired leftovers | `crosier-cleanup` | sonnet |
| Where is X, what calls Y | `crosier-locator` | haiku |
| Refresh `graphify-out/` | `graphify-updater` | sonnet |

How to use them:
- **Pass `subagent_type` and nothing else about the role.** Do not pass `model`;
  the agent file sets it (the hindsight labeller must stay on a different model
  from the Sonnet reviewer).
- **The prompt carries the facts, not the rules.** Goal, exact files and line
  locators already known, the acceptance test, what to return. Paste grep
  results you already have; an agent that has to rediscover them pays for every
  request after.
- **Labelling is script first.** A script writes the batch file with its own
  definitions and output spec; the labeller only reads it and writes verdicts.
  One agent per batch, batches in parallel.
- **No agent for what the main session does cheaper.** Run a benchmark or long
  command with a background Bash call, not through an agent. Read a web page
  with WebFetch inline. Small reads and greps stay inline.
- **No task fits?** Add a new specialist file (tools allowlist, `omitClaudeMd:
  true`, `maxTurns`, report cap) rather than falling back to `general-purpose`,
  and add it to this table.
- **Suggest `/clear` or `/compact` to the user when the topic changes.** Every
  request re-reads the whole main context; a side question asked on top of 150k
  tokens of unrelated work costs 150k per request.

Token care applies to agents too: a corpus or transcript job extracts with a
script first and gives a model only the small slices it must judge.

## Environment

- **Use `py -3`, not `python`.** Bare `python` on this box is 3.10; Crosier
  requires 3.11+ and `hooks/crosier_hook.py` exits 1 below that, so 37 hook
  tests fail under `python` for reasons that are not bugs.
- Tests: `py -3 -m pytest`. `pyproject.toml` pins `testpaths = ["tests"]` and
  excludes `benchmark/`, so a bare run no longer collects the scratch
  `test_runner.py` files the chat benchmark writes under `benchmark/sessions/`.
- Secrets live in `.env` (gitignored). Never print a token value, never commit
  one, never put one in a URL that gets written to `.git/config`.

## What a check costs (measured 2026-09-11, not estimated)

Numbers to quote instead of guessing. All from a real 39,409-char excerpt on
sonnet unless stated.

- Input 19,843 tokens. Of that, 2,385 is the stable cached prefix (system
  prompt plus CLI baseline) and the rest is the excerpt.
- The cached prefix has a **1 hour** TTL and is read at 0.1x, so shortening the
  rubric prompt is not a cost lever. The excerpt is.
- A rendered excerpt is **2.26 chars per token**, not 4: 89.5% of it is tool
  traffic, which tokenizes far denser than prose.
- Composition of a real excerpt: tool_result 59.2% of chars, tool_use 30.3%,
  user 5.6%, assistant 4.7%.
- Production windows average 33,344 chars and hit `EXCERPT_CHAR_CAP` in 9 of 13
  sampled positions. The cap is the operative knob.
- `--effort` decides output tokens and latency: CLI default 8,322 out / 93.0s /
  $0.1676, `medium` 1,893 out / 25.1s / $0.1124, `low` 115 out / 3.7s / $0.0946.
  `config.verdict_effort` sets it and now defaults to `low`, which scores
  27/27 recall, 9/9 category and 0/27 false positives on the synthetic corpus
  against 1 false positive run of 27 for the CLI default. `"none"` in the
  TOML hands the choice back to the CLI.

## Benchmark hazards learned the hard way

- **Padding a case changes its goal.** `build_excerpt` pins a session's first
  user prompt as `[goal]`. `benchmark/haystack.py` grows a small case with real
  filler, and prepending that filler handed the reviewer another session's goal,
  which makes `off_goal` true of every padded case by construction: a run came
  back with a false positive rate of 9/9. Fixed, but the shape of the mistake
  generalises. Any corpus transform has to be checked against what the digest
  pins, not only against what it appends.
- **A too-good or too-bad rate is a harness bug until proven otherwise.** Both
  100% false positives and a perfect score were harness artifacts here before
  they were findings.
- **`drift_visible` is only meaningful at the cap the run actually uses.** It
  was computed at the default 40,000 cap regardless of `--char-cap`, so every
  truncation experiment reported its drift intact.

## Benchmarks

- `benchmark/run.py` — drift benchmark. Scores the zero-context reviewer's
  judgement on fixed excerpts. **Cite the per-run metrics**
  (`recall_per_run`, `false_positive_rate_per_run`), not the per-case ones,
  for any claim that a change moved a rate: per-case scoring is asymmetric on
  purpose and one flip at 3 repeats moves a whole case.
- `benchmark/chat/run_chat_v2.py` — on/off A/B at 24 turns with per-turn
  draft-vs-final scoring (`--agent-effort`, `--reviewer-model`,
  `--reviewer-effort`). Arms: `off`, `on`.
- `benchmark/chat/run_chat.py` — on/off A/B. Runs real agent sessions with
  Crosier enabled and disabled against scenarios with reference solutions.
  Costs real money and hours; check the estimate before starting one.
- A benchmark result is not a finding until the numbers are in `LOGBOOK.md`
  with their denominators.

## Invariants — do not break these without saying so out loud

- **The hook fails open.** On a supported interpreter it always exits 0 and
  prints nothing on internal failure. The single nonzero exit is the
  interpreter version guard, which exists so `plugin.json`'s
  `python3 || python || py -3` chain advances.
- **The hook is the only writer of `SessionState`.** It saves state once per
  invocation, after the gate has returned.
- **The reviewer is zero-context.** Headless calls run with `--system-prompt`,
  `--setting-sources ""`, `--strict-mcp-config`, `--tools ""`,
  `--no-session-persistence`, a dollar cap, and a neutral working directory.
  Measured: a default `claude -p` in the project dir costs 30,906 input tokens
  because it inherits the user's CLAUDE.md, hooks and MCP schemas — the exact
  instructions the reviewer must not share. Isolated: 455 tokens.
- **The excerpt is untrusted input.** It reaches the reviewer through
  `sanitize.py`; verdict strings are scrubbed and capped before they are
  printed back into the main session.
- **Crosier is a Stop gate only.** No background checks (removed 2026-09-13 on
  the user's decision). A risky final answer is reviewed synchronously at Stop
  and a flag returns `decision: block`, so the agent looks again before the turn
  ends. It fails open, runs at most once per turn (`stop_hook_active`), and its
  reason lets the agent keep a correct answer: a note that reads as a challenge
  gets argued with, one that reads as a mandate gets a correct answer
  abandoned.
- **A flag whose evidence quote is not found in the excerpt is demoted** and
  does not interrupt. That mechanical check is what a sampling panel would
  otherwise be bought for.
