"""Load Crosier configuration from an optional .crosier.toml, with safe defaults."""

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
    "call_threshold": 30,
    "turn_threshold": 10,
    "token_threshold": 40_000,
    "repetition_threshold": 0.4,
    "max_checks_per_session": 12,
    "min_calls_between_checks": 8,
    "min_flag_confidence": "medium",
    "staleness_line_limit": 150,
    "call_timeout": 60,
    "worker_deadline": 90,
}

VALID_ANNOUNCE = {"always", "on-flag"}
VALID_CONFIDENCE = {"low", "medium", "high"}


@dataclass
class CrosierConfig:
    enabled: bool = True
    # "always": a clean verdict is shown to the user (never to the agent).
    # "on-flag": nothing is shown unless there is a concern.
    announce: str = "always"
    verdict_model: str = "sonnet"

    # Drift accrues per model call — every one is a chance to build on the
    # model's own previous output — so the primary trigger counts calls (tool
    # batches). Context growth and user turns are secondary triggers.
    call_threshold: int = 30
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


def _coerce(values: dict) -> dict:
    """A hand-edited TOML can hold anything. A bad value degrades to the
    default for that one key rather than taking the user's turn down."""
    if values.get("announce") not in VALID_ANNOUNCE:
        values["announce"] = DEFAULTS["announce"]
    if values.get("min_flag_confidence") not in VALID_CONFIDENCE:
        values["min_flag_confidence"] = DEFAULTS["min_flag_confidence"]
    for key in (
        "call_threshold",
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
    if not isinstance(values.get("enabled"), bool):
        values["enabled"] = DEFAULTS["enabled"]
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
