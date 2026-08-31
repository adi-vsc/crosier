"""Load Crosier configuration from an optional .crosier.toml, with safe defaults."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULTS: dict = {
    "enabled": True,
    "announce": "always",
    "digest_model": "sonnet",
    "verdict_model": "sonnet",
    "turn_threshold": 20,
    "token_threshold": 60_000,
    "repetition_threshold": 0.4,
}


@dataclass
class CrosierConfig:
    enabled: bool = True
    announce: str = "always"
    digest_model: str = "sonnet"
    verdict_model: str = "sonnet"
    turn_threshold: int = 20
    token_threshold: int = 60_000
    repetition_threshold: float = 0.4


def load_config(project_root: Path) -> CrosierConfig:
    values = dict(DEFAULTS)
    config_path = Path(project_root) / ".crosier.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        known_fields = {f.name for f in fields(CrosierConfig)}
        overrides = {k: v for k, v in data.get("crosier", {}).items() if k in known_fields}
        values.update(overrides)
    return CrosierConfig(**values)
