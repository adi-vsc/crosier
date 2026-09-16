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

When the agent is about to end a turn with an answer worth acting on, Crosier
hands a structured excerpt of the session to a fresh, zero-context Claude
instance for a second opinion. If the reviewer flags a problem, the turn is
held and the agent looks again **before the answer stands**, so a correction
is preventive rather than a post-mortem.

## Install

As a Claude Code plugin. Two lines, from inside Claude Code:

```
/plugin marketplace add adi-vsc/crosier
/plugin install crosier@crosier
```

The first line registers this repository as a plugin marketplace (it carries
`.claude-plugin/marketplace.json`); the second installs the plugin from it and
registers its `Stop` hook for you. This is the supported path.

The plugin is self-contained: it needs no `pip install` and no dependencies.

The `crosier` CLI (`status`, `report`) is optional and comes from a
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

No configuration is required — defaults are safe and on by default. At the end
of the first turn of a session Crosier prints one line to your screen saying it
is running, and then says nothing until it has something to say.

## Checking on it, and turning it off

```
crosier status     # on? budget left? what did it last say? where are the files?
crosier report     # every check this session made, and what was flagged
```

Both need the CLI installed (see above).

To switch Crosier off for one session without editing anything, start Claude
with the kill switch set:

```
CROSIER_DISABLED=1 claude
```

The hook checks it before doing any work: no transcript read, no state written,
no output. To switch it off for a project instead, set `enabled = false` in
`.crosier.toml`.

## How it works

1. A single hook script listens on one event, `Stop`. It decides locally, in
   pure Python with no network call, whether the final answer is worth a
   review: the answer claims an outcome (tests pass, done, fixed, ready), or
   the turn edited files. Every other answer passes at zero cost.
2. A risky answer is reviewed **synchronously**, inside the hook. The user's
   prompt waits for one reviewer call, capped at 60 seconds.
3. The excerpt is built mechanically, not by a summarising model. It is a
   chronological, speaker-labelled digest of the recent session: `[user]`,
   `[assistant]`, `[tool_use #N Name]` with argument previews,
   `[tool_result #N | size]` cut to head and tail, with the original
   request pinned on top as `[goal]`, and the final answer as its last record.
4. The hook makes **one** isolated headless `claude` call (Sonnet by
   default) that sees nothing but Crosier's own system prompt and the
   excerpt: no CLAUDE.md, no hooks, no plugins, no MCP servers, no tools, no
   saved session. Measured against a default `claude -p` in a project
   directory, that is 455 input tokens of overhead instead of 30,906, and
   3s instead of 11s.
5. The reviewer answers with a structured verdict naming one of six drift
   modes, or `proceed`. A flag must quote its evidence verbatim from the
   excerpt; the quote is checked mechanically, and a flag whose quote is not
   found is demoted to a guess and never shown to the agent.
6. A flag returns `decision: block`, so the agent revises before the turn
   ends. The reason lets it keep a correct answer: restate it unchanged and
   dismiss the flag in one line. A clean verdict lets the answer stand
   silently. The gate runs at most once per turn: the revision it forces is
   never reviewed again.

The gate fails open. A reviewer call that errors, times out or returns
nothing lets the answer stand.

Known limit: in the interactive terminal the draft has already streamed when
`Stop` fires, so you see the draft and then the correction. Only headless and
SDK sessions truly hide the bad answer.

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

- A blind reviewer does not get to overrule a correct answer, only to make it
  be looked at twice. The block reason says so, and tells the agent it may
  restate the answer unchanged.
- The block reason also forbids arguing with it. Without that, the main agent
  spends its next turn justifying itself, which costs more than the drift did.
- It is told what it flagged last time, so a concern the agent already
  answered is not raised again as if new.
- **Low-confidence flags are suppressed** (`min_flag_confidence`), and a flag
  without verifiable evidence is low-confidence by definition.
- Everything the reviewer sees is **untrusted input**. A tainted
  `package.json` the agent read is in the transcript verbatim, so the excerpt
  is fenced, structural tags are flattened, and override phrases are
  neutralized on the way in. The verdict's free-text fields are scrubbed and
  length-capped on the way out, because they get printed into your session.
- Checks are **budgeted** (`max_checks_per_session`). Each headless call also
  carries a hard dollar cap, and a call past its timeout has its `claude`
  process tree killed.
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
verdict_model = "sonnet"
verdict_effort = "low"          # "low".."max", or "none" for the CLI default
max_checks_per_session = 12     # hard budget per session
min_flag_confidence = "medium"  # "low" | "medium" | "high"
call_timeout = 60               # the headless call, capped at 60 inside the hook
```

An unparseable file, or a bad value for any single key, falls back to the
default for that key rather than taking your turn down with it. Keys from
earlier versions (`call_threshold`, `stop_gate`, `worker_deadline`, ...) are
ignored.

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
