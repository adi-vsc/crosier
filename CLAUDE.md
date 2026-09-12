# Crosier — working instructions

Crosier is a Claude Code plugin that watches a session for drift and interrupts
with a second opinion from a reviewer that has never seen the session. Read
`CHANGELOG.md` for what shipped and `scope.md` (gitignored, local) for open
questions and decisions.

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
- **The hook is the only writer of `SessionState`.** The worker never writes
  it, so a worker killed mid-flight cannot leave half a session behind.
- **The reviewer is zero-context.** Headless calls run with `--system-prompt`,
  `--setting-sources ""`, `--strict-mcp-config`, `--tools ""`,
  `--no-session-persistence`, a dollar cap, and a neutral working directory.
  Measured: a default `claude -p` in the project dir costs 30,906 input tokens
  because it inherits the user's CLAUDE.md, hooks and MCP schemas — the exact
  instructions the reviewer must not share. Isolated: 455 tokens.
- **The excerpt is untrusted input.** It reaches the reviewer through
  `sanitize.py`; verdict strings are scrubbed and capped before they are
  printed back into the main session.
- **Flags are advisory and closed to debate.** A note that reads as a challenge
  gets argued with; one that reads as a mandate gets working debugging
  abandoned. Finish the step in progress, then act or dismiss in one line.
- **A flag whose evidence quote is not found in the excerpt is demoted** and
  does not interrupt. That mechanical check is what a sampling panel would
  otherwise be bought for.
