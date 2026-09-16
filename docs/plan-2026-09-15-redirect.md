# Plan 2026-09-15 — redirect Crosier around what survived the stress test

## Where this came from

A status review proposed a diagnosis (D1-D5) and four fixes (F1-F4). A Sonnet
subagent attacked them with no history. Its kill list, verified by the main session:

| # | Claim | Verdict | Locator |
|---|---|---|---|
| 1 | FP rises with window size 0/9 -> 4/9 at 33k | **Killed.** Retracted in LOGBOOK the same day; all later FPs are `off_goal` by construction; other modes 0/9 | LOGBOOK "Retraction: the haystack was judging..." and "Every false positive ... is off_goal" |
| 2 | D1 nothing to catch (off arm 26/26) | Weakened. n=1, one scenario, haiku low | LOGBOOK "24-turn haiku off/on rerun" |
| 3 | F2 ledger at ~2k tokens | Weakened. Stop hook cannot get a ledger without a prompt change on every turn or a second call | hooks.md (Stop input is `last_assistant_message`) |
| 4 | F1 corpus of user-initiated attacks | Weakened. Survivorship: measures catches the user already made, not missed drift | — |
| 5 | F3 decision-phrase trigger | Weakened. Same class of surface regex as `gate.py` | src/crosier/gate.py |
| 6 | F4 PreToolUse gate | Real cost. No per-turn ceiling like `stop_hook_active` | hooks.md |
| + | Missed: test results as the trigger | New | — |
| + | Null option: ship the manual loop as a command | Survives best | — |

What survives unattacked: D2 (oracle mismatch), D3 (TUI streams before Stop),
D5 (WHEN and WHAT are the hard parts), gate latency and auto-disable (D4 minus #1).

## Principles for this round

- Zero-token work first. Scripts extract; a model sees only small slices it must judge.
- No shipped default changes without a measurement in LOGBOOK.
- No multi-session paid benchmark runs. Small capped probes (< $5 total) allowed.
- Stop-gate-only invariant is kept. PreToolUse is measured, not built.
- Engineers write new modules and tests in disjoint files; one integration pass wires them.

## Workstreams

### W1 — Real-session corpus (F1, attack #4) — zero tokens to extract
`benchmark/corpus/mine_sessions.py`. Input `~/.claude/projects/**/*.jsonl`, skipping
benchmark, probe and claude-mem observer sessions. Output `benchmark/corpus/out/`
(gitignored):
- `turns.jsonl`: one row per user turn: session, turn index, file+line locator,
  user text (capped), final assistant text (capped), tool names used, edit count,
  test commands run and their parsed outcome, char counts by record type.
- `events.jsonl`: labelled events with locators:
  `user_attack_request` (user asks for subagent/stress test/second opinion),
  `agent_attack_call` (assistant Agent call with model sonnet whose prompt reads as
  an attack; keep prompt and returned result, both capped),
  `user_correction` (user says the agent is wrong / retract / that's not what I asked),
  `self_retraction` (assistant retracts or says a finding killed its claim).
- `summary.json`: counts per label, per session.
Then a Sonnet labeller confirms or rejects each candidate event from a <= 1,500-char
slice only. Survivorship (#4) is addressed by `user_correction` and
`self_retraction`, which capture drift found without a subagent, and by a
5-session sample of unlabelled turns read as assistant prose only.

### W2 — Trigger study (F3, #5, #6, missed fix) — zero tokens
`benchmark/corpus/triggers.py` + `eval_triggers.py`. Over `turns.jsonl`, score each
trigger by fire rate per turn and by lead: fired at or before the turn preceding a
confirmed event. Triggers: current `gate.risky_answer_reason`; decision-phrase
regex; test-contradiction (last test run in turn failed and answer claims
success); turn-count baseline (every Nth turn); random at matched fire rate.
Also measures #6: consequential tool calls (git commit/push, Write) per turn.

### W3 — Message-anchored brief (F2, #3)
`src/crosier/brief.py`. No agent-authored ledger (kills #3 and author omission at
once): the brief is built mechanically from what the user will read anyway —
goal, latest user instruction (always, fixes the goal rule in scope.md), the final
answer, and only the tool results in the window that the answer references (paths,
commands, test names, quoted strings), each capped. Offline size comparison
against `build_excerpt` over corpus windows. Wired behind
`review_input = "excerpt" | "brief"`, default `excerpt` until measured.

### W4 — Mechanical contradiction block (missed fix) — zero tokens at runtime
`src/crosier/mechanical.py`. Parses pytest, unittest, npm/jest/vitest, cargo, go test
summaries from this turn's tool results. If the latest run failed and the final
answer claims pass/done, return a block reason quoting the failing summary line,
with no model call. Wired into the gate before the LLM review, behind
`mechanical_block`, default off until W2 measures its false-hit rate.

### W5 — Gate robustness (D4)
- Timeout and auto-disable: a timed-out review must not permanently disable the
  gate for the session after two turns; make disable a cooldown of N turns and emit
  one `systemMessage` when the gate backs off (today it is silent).
- Haiku output blow-up at effort low (6-9.5k output tokens, ~105s): probe whether
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS` or an equivalent bounds a headless call; if it does,
  set it for reviewer calls. Probe cost < $0.50.

### W6 — The null option as a product: `/crosier:attack` (survives best)
`commands/attack.md`. Reproduces the loop that works: the agent writes a brief of the
current decision (options, choice, reasoning, evidence with locators, no history,
quoted as an artifact), spawns a Sonnet subagent with it, and returns the kill list
verified against locators. No CLI needed, so it works from a marketplace install.

### W7 — Base rate from data already paid for (D1, #2) — zero tokens
`benchmark/chat/base_rate.py`: from `chat_results.json` (v1, 24 sessions) and
`chat_v2_results.json`, report per-turn bad rates where derivable and pass rates per
arm with denominators. Plus a prepared-not-run command and estimate for an off-arm-only
base-rate batch across scenarios, for the user to approve.

### W8 — Integration, review, record (Opus)
Wire W3/W4/W5 into `config.py`, `pipeline.py`, `gate.py`, hook. Sonnet diff review. Suite
green under `py -3 -m pytest`. LOGBOOK entries per workstream with numbers. Capped replay
(< $5): reviewer on confirmed corpus positives with `excerpt` vs `brief`, sonnet low.

## Order

Wave 1 in parallel: W1, W4, W5, W6, W7, W3 (module + tests only).
Wave 2 after W1: W1 labeller, W2 evaluation, W3 size comparison.
Wave 3: W8.

## Success criteria

- Corpus: counts of confirmed events with locators, by label.
- Triggers: fire rate and lead for each, on the same denominator.
- Brief: tokens vs excerpt on the same windows; replay agreement with kill lists.
- Mechanical block: false-hit count on corpus turns.
- Suite green; no shipped default changed without a LOGBOOK number.
- Null exit stays live: if no trigger beats random at matched fire rate, the
  recommendation is `/crosier:attack` only, and the gate is parked.

## Second attack (Sonnet, plan only) — accepted changes

1. W3 selection by reference recreates omission. Brief now always carries every
   failure-signalling tool result in the window, plus the last test run, before
   referenced calls. Omission test added.
2. W2 label leakage. Triggers read only turn t's assistant text and tool calls;
   events are labelled from turn t+1's **user** text (`user_attack_request`,
   `user_correction`). `self_retraction` is assistant text and is excluded from
   lead scoring. Report trigger-fire vs candidate-event locator overlap before
   confirmation; overlap near total voids the lead.
3. Null exit made operational: "beats random" means at matched fire rate the
   trigger's hits exceed the random trigger's mean hits over 1,000 draws with
   empirical p < 0.05 and at least 2x the random mean. Fewer than 20 confirmed
   user-labelled events: W2 reports "underpowered", and neither the gate nor the
   null exit is decided by it.
4. W4 claim narrowed to test/verification claims; generic "done" never blocks.
   Default stays off.
5. W5 cooldown counted in skipped reviews; at most two user messages per session.
6. W7 numbers are reported as n-limited and are not a base rate claim.
7. W6 is additive and changes no default; its own criterion is the corpus's
   `agent_attack_call` events: share of attacks whose kill list changed the next
   answer.
