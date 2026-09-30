from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import wx_context.config as config_module
from wx_context.config import load_config
from wx_context.errors import StorageError, ValidationError


def test_allowlist_is_empty_then_persists(tmp_path):
    config = load_config(tmp_path)

    config_path = tmp_path / "config.json"
    assert config_path.exists()
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "allowed_groups": {},
        "schema_version": 1,
    }
    assert config.allowed_groups == {}

    config.allow_group("room-1", "Research")

    assert load_config(tmp_path).allowed_groups == {"room-1": "Research"}


def test_allowlist_rejects_blank_and_duplicate_ids(tmp_path):
    config = load_config(tmp_path)

    with pytest.raises(ValidationError, match="session id"):
        config.allow_group("", "Research")

    config.allow_group("room-1", "Research")

    with pytest.raises(ValidationError, match="already allowlisted"):
        config.allow_group("room-1", "Research")


@pytest.mark.parametrize(
    "payload",
    [
        {"allowed_groups": {}},
        {"schema_version": 2, "allowed_groups": {}},
        {"schema_version": True, "allowed_groups": {}},
    ],
)
def test_load_config_rejects_missing_or_unsupported_schema_version(tmp_path, payload):
    (tmp_path / "config.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StorageError):
        load_config(tmp_path)


def test_allow_group_does_not_mutate_memory_when_save_fails(tmp_path, monkeypatch):
    config = load_config(tmp_path)

    def fail_write(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(config_module, "_write_json_atomically", fail_write)

    with pytest.raises(StorageError):
        config.allow_group("room-1", "Research")

    assert config.allowed_groups == {}
    assert load_config(tmp_path).allowed_groups == {}


def test_stale_configs_merge_independent_allowlist_additions(tmp_path):
    first = load_config(tmp_path)
    second = load_config(tmp_path)

    first.allow_group("room-1", "Research")
    second.allow_group("room-2", "Product")

    assert load_config(tmp_path).allowed_groups == {
        "room-1": "Research",
        "room-2": "Product",
    }


def test_windows_config_write_applies_restrictive_dacl(tmp_path, monkeypatch):
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="DOMAIN\\user\n", stderr="")

    monkeypatch.setattr(config_module, "_is_windows", lambda: True, raising=False)
    monkeypatch.setattr(
        config_module,
        "subprocess",
        SimpleNamespace(run=run),
        raising=False,
    )

    load_config(tmp_path)

    assert commands[0] == ["whoami"]
    assert commands[1][0] == "icacls"
    assert commands[1][1].startswith(str(tmp_path / ".config.json."))
    assert commands[1][2:] == [
        "/inheritance:r",
        "/grant:r",
        "DOMAIN\\user:(F)",
    ]


def test_windows_dacl_failure_rejects_config_write(tmp_path, monkeypatch):
    def run(command, **_kwargs):
        if command == ["whoami"]:
            return SimpleNamespace(returncode=0, stdout="DOMAIN\\user\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="access denied")

    monkeypatch.setattr(config_module, "_is_windows", lambda: True, raising=False)
    monkeypatch.setattr(
        config_module,
        "subprocess",
        SimpleNamespace(run=run),
        raising=False,
    )

    with pytest.raises(StorageError):
        load_config(tmp_path)

    assert not (tmp_path / "config.json").exists()
