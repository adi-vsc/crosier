# Crosier

Install-and-forget direction checks for long Claude Code sessions.

LLM agent sessions measurably degrade as they get longer — this isn't
folklore:

| Finding | Source |
|---|---|
| Across 18 models, performance "degrades as input length increases, often in surprising and non-uniform ways" — well below the context limit | [Chroma, "Context Rot" (Jul 2025)](https://www.trychroma.com/research/context-rot) |
| Every top model tested drops 39% average accuracy in multi-turn vs. single-turn on the same task, and the loss originates in early commitments the model never recovers from | [Laban et al., arXiv:2505.06120](https://arxiv.org/abs/2505.06120) |
| Across 17 models, splitting a question into sequential turns cuts end-to-end accuracy and abstention "by an average of up to 30%, reaching 65% in certain models" (clinical benchmarks) | [Guo et al., arXiv:2603.11394](https://arxiv.org/abs/2603.11394) |
| A parallel LLM monitor "reduced repetition on loop-prone tasks by 52-62% with approximately 11% overhead" — a feasibility study, and its authors note the benefit is task-type dependent | [Khan & Khan, arXiv:2604.13759](https://arxiv.org/abs/2604.13759) |
| "Self-correction works well in tasks that can use reliable external feedback" — which is why the reviewer is told to decide a verification claim on the tool result, not on the prose | [Kamoi et al., arXiv:2406.01297](https://arxiv.org/abs/2406.01297) |

Crosier periodically hands a structured excerpt of your session to a fresh,
zero-context Claude instance for a second opinion, and delivers the answer
back **inside the turn** — next to a tool result, before the agent writes
its answer — so a correction is preventive rather than a post-mortem.

## Install

As a Claude Code plugin. Two lines, from inside Claude Code:

```
/plugin marketplace add adi-vsc/crosier
/plugin install crosier@crosier
```

The first line registers this repository as a plugin marketplace (it carries
`.claude-plugin/marketplace.json`); the second installs the plugin from it and
registers all four hooks for you. This is the supported path.

The plugin is self-contained: it needs no `pip install` and no dependencies.

The `crosier` CLI (`status`, `report`, `check`) is optional and comes from a
checkout — it is not on PyPI yet:

```
git clone https://github.com/adi-vsc/crosier && cd crosier
pip install -e .
```

To register the hooks from a checkout instead of through the marketplace:

```
python3 -m scripts.install
```

The hook entrypoint lives in `hooks/` at the repo root, outside the packaged
`crosier` module, because the plugin loader resolves it from the checkout via
`${CLAUDE_PLUGIN_ROOT}` — so `python3 -m scripts.install` needs the checkout,
not a wheel.

Python 3.11+ is required. Older versions have no `tomllib` and would run with
defaults while silently ignoring your `.crosier.toml`, so the hook declines the
event instead; the install command tries `python3`, then `python`, then `py -3`,
and takes the first one new enough.

No configuration is required — defaults are safe and on by default. On its
first hook event of a session Crosier prints one line to your screen saying it
is running and when the first check is due, and then says nothing until it has
something to say.

## Checking on it, and turning it off

```
crosier status     # on? budget left? what did it last say? where are the files?
crosier report     # every check this session made, and what was flagged
crosier check      # run a direction check now instead of at the next threshold
```

Both need the CLI installed (see above). `crosier check` leaves a request that
the session's next hook event picks up, so the check starts within one model
call. Inside Claude Code the plugin also ships `/crosier:check`, which runs the
same command for you (it needs the CLI too) — the fastest way to see Crosier work on your very first
session rather than waiting for the first threshold.

To switch Crosier off for one session without editing anything, start Claude
with the kill switch set:

```
CROSIER_DISABLED=1 claude
```

The hook checks it before doing any work: no transcript read, no state written,
no output. To switch it off for a project instead, set `enabled = false` in
`.crosier.toml`.

## How it works

1. A single hook script listens on four events. `PostToolBatch` is the
   clock: it fires once per model call, before the next request. It counts
   calls, context growth (from the token counts the transcript already
   carries) and tool-call repetition — pure Python, no network call.
   `UserPromptSubmit` counts turns. `Stop` counts the final call.
   `PreCompact` always forces a check, because compaction is the
   highest-drift-risk moment in a session.
2. When a threshold trips, the hook builds an **excerpt** and hands it to a
   **detached background worker, then returns** — typically in well under
   100ms. It never waits for a model.
3. The excerpt is built mechanically, not by a summarising model. It is a
   chronological, speaker-labelled digest of the recent session: `[user]`,
   `[assistant]`, `[tool_use #N Name]` with argument previews,
   `[tool_result #N | size]` cut to head and tail, with the original
   request pinned on top as `[goal]`. Around 3-4k tokens.
4. The worker makes **one** isolated headless `claude` call (Sonnet by
   default) that sees nothing but Crosier's own system prompt and the
   excerpt: no CLAUDE.md, no hooks, no plugins, no MCP servers, no tools, no
   saved session. Measured against a default `claude -p` in a project
   directory, that is 455 input tokens of overhead instead of 30,906, and
   3s instead of 11s.
5. The reviewer answers with a structured verdict naming one of six drift
   modes, or `proceed`. A flag must quote its evidence verbatim from the
   excerpt; the quote is checked mechanically, and a flag whose quote is not
   found is demoted to a guess and never shown to the agent.
6. The verdict lands in a result file. The **next** hook invocation — usually
   the next `PostToolBatch`, seconds later — picks it up and injects it as
   context next to the tool result. At `Stop`, a flag is delivered as
   feedback that continues the turn so the agent acts on it before the answer
   stands; a clean verdict is never delivered there.

Clean verdicts are shown to you on screen and never to the agent, so a
check that finds nothing costs the session nothing.

## What the reviewer looks for

`unverified_claim` (a verification outcome — tests pass, build succeeds, bug
fixed — decided against the `[tool_result]` lines in the window rather than
against how confidently it is worded: a claim no result supports, or one a
result contradicts), `off_goal` (work no longer serving the original request or
the latest instruction), `ignored_correction`, `research_collapse` (acting on
recalled facts where a cheap check was available), `loop` (same call, same
target, no progress), `padding`.

## What it deliberately does not do

A reviewer that sees only an excerpt is a reviewer that cannot see your
repository, and most of the design is about not letting it act like it can.

- It never tells the agent to abandon work in progress or skip a step that
  failed, and the announcement says so out loud — a flag is advisory, and
  the agent is told to finish the step it is on first.
- The announcement also forbids arguing with it. Without that, the main agent
  spends its next turn justifying itself, which costs more than the drift did.
- It is told what it flagged last time, so a concern the agent already
  answered is not raised again as if new.
- **Low-confidence flags are suppressed** (`min_flag_confidence`), and a flag
  without verifiable evidence is low-confidence by definition.
- A **bulk sweep is not a loop.** Ten `Edit` calls across ten files is a
  refactor; ten across one file is a loop. The repetition signal reads the
  arguments, not just the tool name.
- Everything the reviewer sees is **untrusted input**. A tainted
  `package.json` the agent read is in the transcript verbatim, so the excerpt
  is fenced, structural tags are flattened, and override phrases are
  neutralized on the way in. The verdict's free-text fields are scrubbed and
  length-capped on the way out, because they get printed into your session.
- Checks are **budgeted** (`max_checks_per_session`) and rate-limited
  (`min_calls_between_checks`), and only one worker runs per session at a
  time. Each headless call also carries a hard dollar cap.
- The worker **kills itself** at `worker_deadline`, taking its `claude`
  process tree with it. Force-quitting the main session cannot leave a
  headless process resident.
- A verdict that arrives after the session has moved on (more than
  `staleness_line_limit` transcript lines) is discarded instead of announced.
- If the transcript format stops looking like something Crosier can parse
  (it is internal and undocumented), it backs off and logs rather than
  paying for a review of nothing.

Crosier's own files live under `~/.claude/crosier/` (override with
`CROSIER_HOME`), never inside your project.

## Configuration (all optional)

`.crosier.toml` in your project root:

```toml
[crosier]
enabled = true
announce = "always"          # "always" | "on-flag"  (clean verdicts: on screen only)
verdict_model = "sonnet"

call_threshold = 30          # model calls since the last check
first_check_call_threshold = 12  # the FIRST check of a session fires here
turn_threshold = 10          # user prompts since the last check
token_threshold = 40000      # context growth since the last check
repetition_threshold = 0.4

max_checks_per_session = 12  # hard budget per session
min_calls_between_checks = 8 # cooldown after any check
min_flag_confidence = "medium"  # "low" | "medium" | "high"
staleness_line_limit = 150   # drop a verdict this far behind the session
call_timeout = 60            # the headless call
worker_deadline = 90         # worker self-destructs here
```

An unparseable file, or a bad value for any single key, falls back to the
default for that key rather than taking your turn down with it.

`first_check_call_threshold` exists because the damage a long session never
recovers from is committed early — models "make assumptions in early turns and
prematurely attempt to generate final solutions, on which they overly rely"
([arXiv:2505.06120](https://arxiv.org/abs/2505.06120)) — and under one flat
threshold that stretch is the only part of a session nobody looks at. It moves
the first check earlier; it does not add checks. `max_checks_per_session` and
`min_calls_between_checks` are unchanged and still govern, so a session's total
spend is the same.

`CROSIER_DISABLED=1` in the environment turns Crosier off for that session
regardless of any config file.

## Statistics

The table above cites independent published research on why this problem
is real. Crosier's own effectiveness numbers (catch rate, false-positive
rate, measured token overhead) are not yet published — that requires a
real on/off benchmark across long agentic sessions, which is planned for
a v1.x release rather than asserted at launch.

## License

MIT
