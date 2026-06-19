"""Tests for the task-parameter secret redactor in ``task_engine``
(ADR-012 D12 / u81kh): a manual encrypted DBAS backup/restore passes an operator
``passphrase`` as an ad-hoc parameter; it must NEVER reach a log line or the
journal audit row (which is itself included in backups).
"""
from task_engine import _redact_task_parameters, _param_keys, _REDACTED_PARAM


def test_passphrase_value_is_masked():
    out = _redact_task_parameters({"artifact_path": "/tmp/x.zip", "passphrase": "hunter2-secret"})
    assert out["passphrase"] == _REDACTED_PARAM
    assert out["artifact_path"] == "/tmp/x.zip"  # non-secret keys untouched


def test_match_is_case_insensitive_and_covers_common_secrets():
    out = _redact_task_parameters(
        {"Passphrase": "a", "API_KEY": "b", "token": "c", "client_secret": "d", "name": "keep"}
    )
    assert out["Passphrase"] == _REDACTED_PARAM
    assert out["API_KEY"] == _REDACTED_PARAM
    assert out["token"] == _REDACTED_PARAM
    assert out["client_secret"] == _REDACTED_PARAM
    assert out["name"] == "keep"


def test_empty_and_none_secret_values_are_left_as_is():
    # An empty/None value carries no secret; preserve it so the logged shape is
    # honest (distinguishes "unset" from a masked real value).
    out = _redact_task_parameters({"passphrase": "", "token": None})
    assert out["passphrase"] == ""
    assert out["token"] is None


def test_non_dict_passes_through():
    assert _redact_task_parameters(None) is None
    assert _redact_task_parameters("not-a-dict") == "not-a-dict"


def test_original_dict_is_not_mutated():
    original = {"passphrase": "secret"}
    _redact_task_parameters(original)
    assert original["passphrase"] == "secret"  # only the returned copy is masked


def test_param_keys_returns_only_key_names_never_values():
    # The logger sites log KEY NAMES only — a secret VALUE must never appear, so
    # the generic engine log lines stay clear of the clear-text-logging sink.
    out = _param_keys({"artifact_path": "/tmp/x.zip", "passphrase": "hunter2-secret"})
    assert out == ["artifact_path", "passphrase"]  # sorted key names
    assert "hunter2-secret" not in out


def test_param_keys_passthrough_for_non_dict():
    assert _param_keys(None) is None
