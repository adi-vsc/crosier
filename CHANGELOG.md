# Changelog

## 0.1.0 — 2026-08-31

Initial release.

- `UserPromptSubmit` heuristic scorer (turn count, token estimate, tool-call repetition rate) with configurable thresholds.
- Forced escalation on `PreCompact`.
- Sonnet-generated session digest, capped and delta-scoped (not raw transcript).
- Independent Sonnet verdict call on the digest alone, zero shared context with the main session.
- Announcement on by default, opt-out via `.crosier.toml`.
- Fail-open error handling with 2-strike backoff per session.
- Manual-install fallback script for merging hooks into `settings.json`.
