# tests/test_config.py
from pathlib import Path

from crosier import config as config_module
from crosier.config import CrosierConfig, load_config


def test_defaults_when_no_config_file(tmp_path: Path):
    assert load_config(tmp_path) == CrosierConfig()


def test_default_values(tmp_path: Path):
    config = load_config(tmp_path)
    assert config.enabled is True
    assert config.announce == "always"
    assert config.verdict_model == "sonnet"
    # Trigger units are model calls and context growth. A check now costs one
    # isolated call of a few thousand tokens, so it can afford to be more
    # frequent than the two-call, 60k-token-overhead version it replaces.
    assert config.call_threshold == 30
    assert config.turn_threshold == 10
    assert config.token_threshold == 40_000
    assert config.repetition_threshold == 0.4
    assert config.max_checks_per_session == 12
    assert config.min_calls_between_checks == 8
    assert config.min_flag_confidence == "medium"
    # One user turn in an autonomous session is 15 to 260 transcript lines
    # (measured); a verdict consumed within one tool batch is rarely more than
    # a few dozen behind. The limit is for the case where nothing consumed it.
    assert config.staleness_line_limit == 150
    assert config.call_timeout == 60
    # The worker must be able to finish its one call before it self-destructs.
    assert config.worker_deadline >= config.call_timeout + 15


def test_overrides_from_toml_file(tmp_path: Path):
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\n'
        'announce = "on-flag"\n'
        'call_threshold = 12\n'
    )
    config = load_config(tmp_path)
    assert config.announce == "on-flag"
    assert config.call_threshold == 12
    assert config.enabled is True  # untouched fields keep their default


def test_malformed_toml_falls_back_to_defaults(tmp_path: Path):
    # load_config runs at the top of the hook, before any guard. A typo in
    # .crosier.toml must not take the user's turn down with it.
    (tmp_path / ".crosier.toml").write_text('[crosier\nenabled = tru', encoding="utf-8")
    assert load_config(tmp_path) == CrosierConfig()


def test_unreadable_toml_falls_back_to_defaults(tmp_path: Path):
    (tmp_path / ".crosier.toml").mkdir()
    assert load_config(tmp_path) == CrosierConfig()


def test_toml_without_crosier_table_falls_back_to_defaults(tmp_path: Path):
    (tmp_path / ".crosier.toml").write_text('[other]\nfoo = 1\n', encoding="utf-8")
    assert load_config(tmp_path) == CrosierConfig()


def test_unknown_and_retired_keys_in_toml_are_ignored(tmp_path: Path):
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\n'
        'nonsense_field = "whatever"\n'
        'digest_model = "haiku"\n'
        'staleness_seconds = 300\n'
        'min_turns_between_checks = 5\n'
    )
    config = load_config(tmp_path)  # must not raise
    assert config == CrosierConfig()


def test_bad_value_falls_back_per_key_not_wholesale(tmp_path: Path):
    # One nonsense value should cost the user that one setting, not the four
    # valid ones next to it.
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\n'
        'announce = "sometimes"\n'
        'min_flag_confidence = "certain"\n'
        'call_threshold = "twenty"\n'
        'max_checks_per_session = -3\n'
        'verdict_model = "opus"\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.announce == "always"
    assert config.min_flag_confidence == "medium"
    assert config.call_threshold == 30
    assert config.max_checks_per_session == 12
    assert config.verdict_model == "opus"


def test_without_tomllib_the_defaults_still_load(tmp_path: Path, monkeypatch):
    # The plugin's hook command can land on a Python 3.10 (`python` on a stock
    # Windows box). There `import tomllib` raised on every event and Crosier
    # silently never ran. Without a parser the config file is ignored, not
    # fatal.
    (tmp_path / ".crosier.toml").write_text('[crosier]\ncall_threshold = 3\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "tomllib", None)
    assert load_config(tmp_path) == CrosierConfig()
