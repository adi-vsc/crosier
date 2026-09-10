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
- Tests: `py -3 -m pytest tests/` — scope it to `tests/`, because a bare
  `pytest` also collects the scratch `test_runner.py` files the chat benchmark
  writes under `benchmark/sessions/` and dies with a usage error.
- Secrets live in `.env` (gitignored). Never print a token value, never commit
  one, never put one in a URL that gets written to `.git/config`.

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
