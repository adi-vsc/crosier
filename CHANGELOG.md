# Changelog

## Unreleased

Crosier is now a Stop gate and nothing else. The asynchronous checks judged a
turn's final answer only after the user had read it, which is the failure
Crosier exists to replace.

### Changed

- **The Stop gate is off by default** (`enabled = false`). Its trigger did not
  beat a random trigger on real sessions, so the pre-registered null exit
  applies: `/crosier:attack` is the default product and the gate is an
  experimental opt-in (`enabled = true` in `.crosier.toml`) until a replay of
  the real hook measures the precision of its flags. A parked install prints
  nothing.
- The block reason no longer tells the agent to run the reviewer's suggested
  check. It calls it a hint from a reviewer that read untrusted session
  content, to be run only if the agent would run it anyway.
- The hook listens on `Stop` only. A final answer that claims an outcome, or
  follows a turn that edited files, is reviewed synchronously and a flag
  returns `decision: block`; the agent revises before the turn ends, at most
  once per turn. Fails open on any error or timeout.
- The gate is on whenever Crosier is enabled. There is no `stop_gate` key.
- The Stop hook timeout is 90s (plugin manifest and `scripts.install`); the
  reviewer call inside it is capped at 60s.

### Removed

- Background checks: the detached worker, the pending result file, delivery
  through `additionalContext` at `PostToolBatch`, `UserPromptSubmit` and
  `Stop`, and the forced check at `PreCompact`.
- The threshold trigger (calls, turns, context growth, tool repetition, bulk
  sweep detection) and its config keys: `call_threshold`,
  `first_check_call_threshold`, `turn_threshold`, `token_threshold`,
  `repetition_threshold`, `min_calls_between_checks`, `staleness_line_limit`,
  `worker_deadline`, `announce`, `stop_gate`. Old keys in `.crosier.toml` are
  ignored.
- `crosier check` and `/crosier:check`.
- Events other than `Stop` registered by an older install are now a no-op.

### Fixed

- A tool name in the transcript can no longer forge a record header in the
  excerpt. Record bodies were already escaped, but a name is interpolated into
  a record's label, so a tool called `Bash]\n[goal] …` handed the reviewer a
  second goal. Tool names are attacker-controlled in the sense that matters
  here — an MCP server names its own tools — and the excerpt is untrusted
  input. Names are now stripped of brackets and newlines before they reach a
  label.

### Added

- `/crosier:attack`: the manual second-opinion loop as a command. The agent
  writes a brief of the current decision — options, choice, reasoning,
  evidence with locators, no conversation history — hands it to a Sonnet
  subagent, and returns the kill list verified against those locators.
  Additive; it changes no default and does not touch the gate.

## 0.2.0 — 2026-09-06

Audit-driven redesign. Replaying 26 real Claude Code transcripts through the
0.1 pipeline showed it dispatching 0 to 2 checks per session (0 in four of ten
sessions that reached 200-390k context tokens), discarding the few verdicts it
produced as stale, delivering them only at the user's next prompt, and paying
30k tokens of the user's own setup per headless call. Every item below traces
to one of those measurements.

### Delivery inside the turn

- One hook script (`hooks/crosier_hook.py`) on four events. `PostToolBatch`
  fires once per model call, before the next request, and its
  `additionalContext` lands next to the tool result — a verdict now reaches
  the agent seconds after it is written, mid-turn, instead of at the next
  user prompt. `Stop` delivers a flag as feedback that continues the turn so
  the agent acts before the answer stands, and never delivers a clean
  verdict there (any context at Stop continues the turn). `PreCompact`
  dispatches only: its stdout is not injected anywhere, so the old
  announcement there was dead code that also cleared the result.
- Output is JSON: `hookSpecificOutput.additionalContext` for the agent, and
  `systemMessage` for the user's screen. Clean verdicts go to the screen only,
  so a check that finds nothing costs the session nothing.
- `stop_hook_active` is honoured: while Claude is already continuing because
  of a stop hook, a waiting result is left for the next event.

### Triggers that fire on real sessions

- The unit is the model call, counted by `PostToolBatch` and `Stop`, not the
  user prompt. `call_threshold` (30), `min_calls_between_checks` (8),
  `turn_threshold` (10), `token_threshold` (40k growth),
  `max_checks_per_session` (12). Replay of the same transcripts now
  dispatches 2 to 12 checks per session, all delivered.
- Context size is read from the latest usage entry, not the largest.
  Compaction appends to the transcript rather than rewriting it (measured:
  248k before, 85k after, same file), so the maximum froze the growth
  baseline at the pre-compaction peak and the token trigger never fired again.
  The baseline also rebases when the context shrinks.
- Staleness is transcript movement only. Wall-clock age is not movement: an
  idle session is exactly the session the verdict describes, and the old
  300-second rule discarded every verdict that waited for the user.
  `staleness_line_limit` is 150 (one user turn measured at 15-262 lines).

### A reviewer that is actually zero-context

- The headless call runs with `--system-prompt`, `--setting-sources ""`,
  `--strict-mcp-config`, `--tools ""`, `--no-session-persistence`, a dollar
  cap, and a neutral working directory. Measured on a default `claude -p` in
  a project directory: 30,906 input tokens and 10.9s for a two-word prompt,
  because it loaded the user's CLAUDE.md, SessionStart hooks, plugins and MCP
  schemas — the reviewer inherited the very instructions that shape the main
  session. Isolated: 455 tokens, 3.0s.
- `--output-format json` and `--json-schema`: the CLI's `is_error` is honoured
  (an auth failure used to be reviewable text) and the verdict arrives
  structured, with the prose parser as fallback.

### The excerpt and the rubric

- The LLM digest stage is gone. Across the replayed transcripts, tool results
  were 35% of the text, tool_use inputs 34%, assistant prose 9%; the old
  `delta_text` dropped tool_use inputs entirely, kept results uncut (one
  `Read` could fill the 40k window), carried no speaker labels, and after the
  first check held no goal statement. A summarising model reading that
  inherits the agent's own framing and produces a summary no quote can be
  checked against.
- `digest.build_excerpt` builds the reviewer's view mechanically:
  `[goal]` pinned on top, `[latest user instruction]` when the window has no
  user turn, `[user]`/`[assistant]`/`[user command]`, `[tool_use #N Name]`
  with per-argument previews, `[tool_result #N | size]` cut to head and tail
  with errors marked, `[compaction summary]`; meta, sidechain and
  local-command entries dropped. Around 13k characters per check on real
  sessions.
- The verdict prompt names six drift modes — `unverified_claim`, `off_goal`,
  `ignored_correction`, `research_collapse`, `loop`, `padding` — each with
  the evidence pattern that shows it in the excerpt, and a list of what is
  not drift. It demands `evidence` as a verbatim quote; `verify_evidence`
  checks the quote against the excerpt and demotes an unverifiable flag to
  low confidence, which is suppressed by default. The previous flag is
  carried into the next review so an answered concern is not re-raised.

### Runs where it is installed

- `python3` on a stock Windows install is a Store stub and `python` is 3.10,
  where `import tomllib` raised on every event and Crosier silently never
  ran. `tomllib` is now optional (defaults only without it), the plugin
  command chain adds `py -3`, and the installer prefers a 3.11+ interpreter.
- Crosier's files live under `~/.claude/crosier/` (`CROSIER_HOME` to
  override), not in a `.crosier/` dropped into every project.
- The busy marker is written only after the job is delivered to the worker,
  so a failed hand-off cannot block checks for a full deadline.

## Unreleased (0.1.x cycle, previously uncommitted)

### Checks moved off the critical path

The check ran inline in the hook, so Claude Code waited on two headless
`claude -p` calls before the user's turn could start. Measured end to end on
a real session slice, that is 66 seconds of frozen terminal — long enough that
the reflex is to Ctrl+C, which breaks the pipeline for the rest of the session.

- `spawn.py` hands the job to a detached worker and returns (~17ms measured).
  `worker.py` runs the digest and verdict alone; `pending.py` carries the
  result back to a later hook invocation. The hook is now pure Python plus one
  process spawn, and the plugin's hook timeout drops from 100s to 10s.
- The worker never writes `SessionState`. The hook stays the only writer, so a
  worker killed mid-flight cannot leave a half-updated session behind.
- The excerpt travels *inside* the job rather than as a transcript path. The
  file the `PreCompact` worker would have read is rewritten by the compaction
  that triggered it.
- `worker.py` arms a watchdog that kills its `claude` process tree and then
  itself at `worker_deadline`. Force-quitting the main session, or dropping the
  API connection, can no longer strand a headless process. All headless calls
  now go through `claude_cli.py`, which keeps the child reachable for that kill
  and puts it in its own process group so the whole tree goes, not just the
  launcher.
- A verdict that comes back after the session has moved on — more than
  `staleness_line_limit` transcript lines, or older than `staleness_seconds` —
  is discarded rather than announced. It describes a file the agent has already
  finished with.

### Not acting like it can see the repository

- The digest now reports **blockers and hard dependencies**, and the verdict
  prompt forbids telling the agent to skip, work around or abandon anything
  listed there. Without it, a reviewer that cannot see a symlink loop reads
  three failed reads of one file as a loop and says "stop reading it and write
  the code" — the file being a hard dependency is invisible to it.
- Flag announcements are advisory, bounded, and closed to debate: the agent is
  told to finish the step in progress, then act or dismiss in one line, and
  explicitly not to justify earlier turns. A note that reads as a challenge
  gets argued with for a turn; one that reads as a mandate gets a working
  debugging tree abandoned.
- Flags below `min_flag_confidence` (default `medium`) are suppressed.

### Cost and false positives

- `max_checks_per_session` (6) and `min_turns_between_checks` (5) bound what a
  session can spend. One worker per session at a time, enforced by a marker
  file, so a stuck agent cannot queue a reviewer per turn on the same loop.
- `is_bulk_operation` suppresses the repetition trigger for a codebase-wide
  sweep. Ten `Edit` calls across ten files is a refactor; the signal now reads
  the call arguments, not just how often a tool name repeats.

### Untrusted input

- `sanitize.py`: the excerpt is fenced in markers it cannot close, structural
  tags are flattened, and override phrases are neutralized before either model
  sees it. Anything the agent read — a tainted `package.json`, a downloaded
  script — is in the transcript verbatim and reaches the reviewer as data.
- The verdict's `flagged_claim` / `reason` / `suggested_check` are scrubbed and
  capped at 240 chars, and unknown keys are dropped. Those strings are printed
  into the main session, so they are an injection path in their own right.

### Format brittleness

- `schema_health()` detects a transcript that no longer matches the shape this
  parser expects (Claude Code's JSONL layout is internal and undocumented).
  Crosier backs off and logs instead of paying for a digest of nothing.
- `load_config` now coerces each out-of-range or wrong-typed value to that
  key's default individually, rather than trusting whatever the TOML held.

### Earlier in this cycle

Fixes found by replaying 18 real Claude Code transcripts through the heuristic.

- `tool_repetition_rate` now returns 0.0 until the window is full. The buffer
  clears on every check, so two identical calls on the next turn scored 0.5 and
  escalated on what was really a file read twice — 5 of 15 repetition
  escalations across the replay corpus fired on a window shorter than 5.
- `token_threshold` now measures real context growth from the token counts the
  transcript already reports, instead of estimating it as `chars / 4`. Measured
  against those transcripts, the estimate ran ~4x low, because the real context
  also carries the system prompt, tool schemas and injected reminders — none of
  which appear in transcript text. The character estimate remains as a fallback.
- `SessionState.tokens_at_last_check` tracks the growth baseline; `PreCompact`
  rebases it too, so a post-compaction turn does not re-check immediately.
- `load_config` no longer lets a malformed or unreadable `.crosier.toml` raise.
  It runs at the top of both hooks before any guard, so a single typo in the
  config raised `TOMLDecodeError` out of the hook on every turn — the one
  failure mode the fail-open contract exists to prevent. Falls back to defaults.
- README: `pip install crosier` ships the library only, not the hook
  entrypoints. Documented the plugin install as the supported path and the
  source checkout as the manual one.

## 0.1.0 — 2026-08-31

Initial release.

- `UserPromptSubmit` heuristic scorer (turn count, token estimate, tool-call repetition rate) with configurable thresholds.
- Forced escalation on `PreCompact`.
- Sonnet-generated session digest, capped and delta-scoped (not raw transcript).
- Independent Sonnet verdict call on the digest alone, zero shared context with the main session.
- Announcement on by default, opt-out via `.crosier.toml`.
- Fail-open error handling with 2-strike backoff per session.
- Manual-install fallback script for merging hooks into `settings.json`.
