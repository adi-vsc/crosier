# tests/test_config.py
from pathlib import Path

from crosier import config as config_module
from crosier.config import CrosierConfig, disabled_by_env, load_config


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


def test_stop_gate_defaults_off_and_can_be_enabled(tmp_path: Path):
    # The gate blocks a turn, which breaks the advisory-only invariant; nobody
    # gets that without asking for it.
    assert load_config(tmp_path).stop_gate is False
    (tmp_path / ".crosier.toml").write_text('[crosier]\nstop_gate = true\n')
    assert load_config(tmp_path).stop_gate is True


def test_a_non_boolean_stop_gate_stays_off(tmp_path: Path):
    (tmp_path / ".crosier.toml").write_text('[crosier]\nstop_gate = "yes"\n')
    assert load_config(tmp_path).stop_gate is False


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


def test_first_check_threshold_has_a_default_and_is_configurable(tmp_path):
    assert CrosierConfig().first_check_call_threshold == 12
    (tmp_path / ".crosier.toml").write_text(
        """[crosier]
first_check_call_threshold = 5
""",
        encoding="utf-8",
    )
    assert load_config(tmp_path).first_check_call_threshold == 5


def test_a_bad_first_check_threshold_falls_back_to_the_default(tmp_path):
    (tmp_path / ".crosier.toml").write_text(
        """[crosier]
first_check_call_threshold = "soon"
""",
        encoding="utf-8",
    )
    assert load_config(tmp_path).first_check_call_threshold == 12


def test_kill_switch_env_var(monkeypatch):
    # A one-session kill switch that only half-works is worse than none.
    monkeypatch.delenv("CROSIER_DISABLED", raising=False)
    assert disabled_by_env() is False
    for off in ("", "0", "false", "FALSE", "no", "  "):
        monkeypatch.setenv("CROSIER_DISABLED", off)
        assert disabled_by_env() is False, off
    for on in ("1", "true", "yes", "anything"):
        monkeypatch.setenv("CROSIER_DISABLED", on)
        assert disabled_by_env() is True, on


def test_verdict_effort_defaults_to_low(tmp_path):
    # Measured on the 18-case synthetic corpus at 3 repeats: low scores 27/27
    # recall, 9/9 category and 0/27 false positives, against 1 false positive
    # run of 27 for the CLI default on the same corpus. It also returns in
    # 3.7s on a cap-sized excerpt where the default takes 93.0s, which is past
    # the worker deadline that is supposed to bound it.
    assert load_config(tmp_path).verdict_effort == "low"


def test_verdict_effort_accepts_a_level(tmp_path):
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\nverdict_effort = "low"\n', encoding="utf-8"
    )
    assert load_config(tmp_path).verdict_effort == "low"


def test_verdict_effort_can_be_turned_off_to_get_the_cli_default(tmp_path):
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\nverdict_effort = "none"\n', encoding="utf-8"
    )
    assert load_config(tmp_path).verdict_effort is None


def test_an_unknown_verdict_effort_degrades_to_the_default(tmp_path):
    (tmp_path / ".crosier.toml").write_text(
        '[crosier]\nverdict_effort = "turbo"\n', encoding="utf-8"
    )
    assert load_config(tmp_path).verdict_effort == "low"
