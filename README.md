# Crosier

Install-and-forget direction checks for long Claude Code sessions.

LLM agent sessions measurably degrade as they get longer — this isn't
folklore:

| Finding | Source |
|---|---|
| 18 frontier models show monotonically decreasing accuracy as input length grows, even well below their context limit | [Chroma, "Context Rot" (Jul 2025)](https://www.trychroma.com/research/context-rot) |
| Every top model tested drops 39% average accuracy in multi-turn vs. single-turn on the same task | [arXiv:2505.06120](https://arxiv.org/abs/2505.06120) |
| Sequential multi-turn presentation cuts accuracy/abstention ~30% on average, up to 65% in some models | [arXiv:2603.11394](https://arxiv.org/abs/2603.11394) |
| A parallel LLM monitor cuts agent looping 52-62% at ~11% token overhead | [arXiv:2604.13759](https://arxiv.org/abs/2604.13759) |

Crosier periodically hands a compressed digest of your session to a fresh,
zero-context Claude instance for a second opinion — and does it for a
fraction of that 11% overhead, because it's periodic, not continuous.

## Install

```
pip install crosier
python3 -m scripts.install
```

Or install as a Claude Code plugin from the marketplace (see
`.claude-plugin/plugin.json`).

No configuration is required — defaults are safe and on by default.

## How it works

1. `UserPromptSubmit` fires every turn and scores three free signals
   (turn count, token estimate, tool-call repetition rate) — pure Python,
   no network call.
2. When the score crosses a threshold (or `PreCompact` fires, which always
   forces a check), Crosier shells out to your own `claude` CLI twice:
   once to compress the recent session into a ~1-2K token digest (Sonnet),
   once for an independent verdict on that digest alone (fresh Sonnet,
   zero shared context).
3. The verdict is printed to the hook's stdout, which Claude Code injects
   as context on your next turn — the same mechanism you can see in any
   Claude Code hook.

## Configuration (all optional)

`.crosier.toml` in your project root:

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

## Statistics

The table above cites independent published research on why this problem
is real. Crosier's own effectiveness numbers (catch rate, false-positive
rate, measured token overhead) are not yet published — that requires a
real on/off benchmark across long agentic sessions, which is planned for
a v1.x release rather than asserted at launch.

## License

MIT
