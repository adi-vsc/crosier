"""Load Crosier configuration from an optional .crosier.toml, with safe defaults."""

import os
from dataclasses import dataclass, fields
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11: the config file is ignored, never fatal.
    tomllib = None

DEFAULTS: dict = {
    "enabled": True,
    "announce": "always",
    "verdict_model": "sonnet",
    "verdict_effort": "low",
    "call_threshold": 30,
    "first_check_call_threshold": 12,
    "turn_threshold": 10,
    "token_threshold": 40_000,
    "repetition_threshold": 0.4,
    "max_checks_per_session": 12,
    "min_calls_between_checks": 8,
    "min_flag_confidence": "medium",
    "staleness_line_limit": 150,
    "call_timeout": 60,
    "worker_deadline": 90,
    "stop_gate": False,
}

VALID_ANNOUNCE = {"always", "on-flag"}
VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}
# What a user writes to opt out: TOML has no null, so the level that means
# "let the CLI decide" needs a spelling.
EFFORT_CLI_DEFAULT = "none"
VALID_CONFIDENCE = {"low", "medium", "high"}

DISABLE_ENV = "CROSIER_DISABLED"


def disabled_by_env() -> bool:
    """A one-session kill switch that needs no file and no restart of anything.

    Checked before any other work, so `CROSIER_DISABLED=1 claude` costs a
    session exactly one environment lookup per hook invocation.
    """
    return os.environ.get(DISABLE_ENV, "").strip().lower() not in ("", "0", "false", "no")


@dataclass
class CrosierConfig:
    enabled: bool = True
    # "always": a clean verdict is shown to the user (never to the agent).
    # "on-flag": nothing is shown unless there is a concern.
    announce: str = "always"
    verdict_model: str = "sonnet"
    # The reviewer's thinking budget, passed to `claude --effort`. The CLI
    # default measured 8,322 output tokens and 93.0s on a cap-sized excerpt,
    # past `worker_deadline`, so the verdict that call paid for was thrown
    # away. "low" returns the same excerpt in 3.7s, and on the 18-case
    # synthetic corpus at 3 repeats it scores 27/27 recall, 9/9 category and
    # 0/27 false positives against 1 false positive run of 27 for the CLI
    # default. Set it to "none" to hand the choice back to the CLI.
    verdict_effort: str | None = "low"

    # Drift accrues per model call — every one is a chance to build on the
    # model's own previous output — so the primary trigger counts calls (tool
    # batches). Context growth and user turns are secondary triggers.
    call_threshold: int = 30
    # Multi-turn degradation originates in early commitments the session never
    # recovers from (arXiv:2505.06120), so the first check of a session fires
    # sooner than the steady-state cadence. It does not raise the budget:
    # `max_checks_per_session` and `min_calls_between_checks` still govern.
    first_check_call_threshold: int = 12
    turn_threshold: int = 10
    token_threshold: int = 40_000
    repetition_threshold: float = 0.4

    # A check costs one isolated headless call on top of the tokens the main
    # session is already burning, so the session gets a hard budget and a
    # cooldown rather than one check per trigger.
    max_checks_per_session: int = 12
    min_calls_between_checks: int = 8

    # A low-confidence flag from a reviewer that cannot see the repository is
    # noise, and acting on noise costs a working train of thought.
    min_flag_confidence: str = "medium"

    # A verdict describes a moment that has already passed. Past this many
    # transcript lines the session has moved on far enough that the critique
    # is about something else.
    staleness_line_limit: int = 150

    call_timeout: int = 60
    worker_deadline: int = 90

    # Review a risky final answer synchronously at Stop and block the turn on a
    # flag, so the agent revises before the answer stands. Off by default: it
    # holds the user's prompt for the length of a reviewer call, and it is the
    # one place Crosier's verdict is not merely advisory.
    stop_gate: bool = False


def _coerce(values: dict) -> dict:
    """A hand-edited TOML can hold anything. A bad value degrades to the
    default for that one key rather than taking the user's turn down."""
    if values.get("announce") not in VALID_ANNOUNCE:
        values["announce"] = DEFAULTS["announce"]
    if values.get("min_flag_confidence") not in VALID_CONFIDENCE:
        values["min_flag_confidence"] = DEFAULTS["min_flag_confidence"]
    for key in (
        "call_threshold",
        "first_check_call_threshold",
        "turn_threshold",
        "token_threshold",
        "max_checks_per_session",
        "min_calls_between_checks",
        "staleness_line_limit",
        "call_timeout",
        "worker_deadline",
    ):
        value = values.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            values[key] = DEFAULTS[key]
    if not isinstance(values.get("repetition_threshold"), (int, float)):
        values["repetition_threshold"] = DEFAULTS["repetition_threshold"]
    if not isinstance(values.get("verdict_model"), str) or not values["verdict_model"]:
        values["verdict_model"] = DEFAULTS["verdict_model"]
    if values.get("verdict_effort") == EFFORT_CLI_DEFAULT:
        values["verdict_effort"] = None
    elif values.get("verdict_effort") not in VALID_EFFORT:
        values["verdict_effort"] = DEFAULTS["verdict_effort"]
    for key in ("enabled", "stop_gate"):
        if not isinstance(values.get(key), bool):
            values[key] = DEFAULTS[key]
    return values


def load_config(project_root: Path) -> CrosierConfig:
    values = dict(DEFAULTS)
    config_path = Path(project_root) / ".crosier.toml"
    if tomllib is None or not config_path.exists():
        return CrosierConfig(**values)
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError, ValueError):
        # This runs before any guard in the hook, so a typo'd or unreadable
        # config must degrade to defaults rather than take the turn down.
        return CrosierConfig(**values)
    table = data.get("crosier", {}) if isinstance(data, dict) else {}
    if not isinstance(table, dict):
        return CrosierConfig(**values)
    known_fields = {f.name for f in fields(CrosierConfig)}
    values.update({k: v for k, v in table.items() if k in known_fields})
    return CrosierConfig(**_coerce(values))
