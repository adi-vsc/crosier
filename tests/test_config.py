# tests/test_config.py
from pathlib import Path

from crosier.config import CrosierConfig, load_config


def test_defaults_when_no_config_file(tmp_path: Path):
    config = load_config(tmp_path)
    assert config == CrosierConfig(
        enabled=True,
        announce="always",
        digest_model="sonnet",
        verdict_model="sonnet",
        turn_threshold=20,
        token_threshold=60_000,
        repetition_threshold=0.4,
    )


def test_overrides_from_toml_file(tmp_path: Path):
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\n'
        'announce = "on-flag"\n'
        'turn_threshold = 10\n'
    )
    config = load_config(tmp_path)
    assert config.announce == "on-flag"
    assert config.turn_threshold == 10
    assert config.enabled is True  # untouched fields keep their default


def test_unknown_keys_in_toml_are_ignored(tmp_path: Path):
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\n'
        'nonsense_field = "whatever"\n'
    )
    config = load_config(tmp_path)  # must not raise
    assert config.enabled is True
