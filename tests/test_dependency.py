from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from wx_context.dependency import WechatCli
from wx_context.errors import DependencyError
from wx_context.media_index import WechatMediaLocator
from wx_context.models import GroupSession


class RecordingRunner:
    def __init__(
        self,
        stdout: object,
        *,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[Any]:
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            args=command,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def _runner_for(payload: object, **kwargs: object) -> RecordingRunner:
    return RecordingRunner(json.dumps(payload), **kwargs)


def _cli(
    runner: RecordingRunner,
    executable: str | os.PathLike[str] = "fake-wechat-cli",
) -> WechatCli:
    return WechatCli(executable, runner=runner)


def test_groups_keeps_only_group_sessions() -> None:
    runner = _runner_for(
        [
            {"session_id": "room-1", "display_name": "Research", "is_group": True},
            {"session_id": "person-1", "display_name": "Alice", "is_group": False},
        ]
    )

    groups = _cli(runner).groups()

    assert groups == [GroupSession("room-1", "Research", True)]
    assert runner.calls[0][0] == ["fake-wechat-cli", "sessions"]


def test_groups_accepts_current_wechat_cli_session_schema() -> None:
    runner = _runner_for(
        [
            {"chat": "Research", "username": "room@chatroom", "is_group": True},
            {"chat": "Alice", "username": "person-1", "is_group": False},
        ]
    )

    groups = _cli(runner).groups()

    assert groups == [GroupSession("room@chatroom", "Research", True)]


def test_adapter_requests_utf8_child_output() -> None:
    runner = _runner_for([])

    _cli(runner).groups()

    assert runner.calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"  # type: ignore[index]


@pytest.mark.parametrize("marker", ["true", "false", 1, 0, None])
def test_groups_rejects_non_boolean_group_marker(marker: object) -> None:
    runner = _runner_for(
        [{"session_id": "room-1", "display_name": "Research", "is_group": marker}]
    )

    with pytest.raises(DependencyError, match="group marker"):
        _cli(runner).groups()


def test_history_page_uses_only_bounded_history_command() -> None:
    runner = _runner_for([{"id": "message-1", "text": "hello"}])

    result = _cli(runner, Path("fake-wechat-cli")).history_page(
        "room-1", "2026-09-01", "2026-09-07", limit=50, offset=100
    )

    assert result == [{"id": "message-1", "text": "hello"}]
    command, options = runner.calls[0]
    assert command == [
        "fake-wechat-cli",
        "history",
        "room-1",
        "--start-time",
        "2026-09-01",
        "--end-time",
        "2026-09-07",
        "--limit",
        "50",
        "--offset",
        "100",
    ]
    assert options["shell"] is False
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["encoding"] == "utf-8"
    assert options["errors"] == "strict"
    assert options["timeout"] == 30
    assert options["check"] is False
    assert options["env"]["PYTHONUTF8"] == "1"  # type: ignore[index]


def test_history_page_can_request_media_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner_for([{"id": "message-1", "text": "hello"}])

    _cli(runner).history_page(
        "room-1", "2026-09-01", "2026-09-07", limit=50, offset=0
    )

    def unavailable_index(_cls: type[WechatMediaLocator]) -> WechatMediaLocator:
        raise RuntimeError("no local index")

    monkeypatch.setattr(
        WechatMediaLocator,
        "from_current_config",
        classmethod(unavailable_index),
    )
    media_runner = _runner_for([{"id": "message-1", "text": "hello"}])
    WechatCli("fake-wechat-cli", runner=media_runner, include_media=True).history_page(
        "room-1", "2026-09-01", "2026-09-07", limit=50, offset=0
    )

    assert "--media" not in runner.calls[0][0]
    assert media_runner.calls[0][0][-1] == "--media"


def test_history_page_normalizes_current_wechat_cli_message_envelope() -> None:
    runner = _runner_for(
        {
            "chat": "Research",
            "username": "room@chatroom",
            "is_group": True,
            "messages": [
                "[2026-09-01 10:00] Alice: hello",
                "[2026-09-01 10:01] Alice: follow-up\nwithout sender",
            ],
        }
    )

    result = _cli(runner).history_page(
        "room@chatroom", "2026-09-01", "2026-09-07", limit=50, offset=0
    )

    assert result == [
        {
            "timestamp": "2026-09-01T10:00:00",
            "sender": "Alice",
            "text": "hello",
            "type": "text",
        },
        {
            "timestamp": "2026-09-01T10:01:00",
            "sender": "Alice",
            "text": "follow-up\nwithout sender",
            "type": "text",
        },
    ]


def test_history_page_extracts_image_path_from_media_message() -> None:
    path = r"C:\WeChat\Img\image_t.dat"
    runner = _runner_for(
        {
            "chat": "Research",
            "username": "room@chatroom",
            "is_group": True,
            "messages": [f"[2026-09-01 10:00] Alice: [图片] {path}"],
        }
    )

    result = _cli(runner).history_page(
        "room@chatroom", "2026-09-01", "2026-09-07", limit=50, offset=0
    )

    assert result == [
        {
            "timestamp": "2026-09-01T10:00:00",
            "sender": "Alice",
            "text": "[图片]",
            "type": "image",
            "media_path": path,
        }
    ]


def test_history_page_replaces_adapter_sample_path_with_message_resource_path(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "correct-image.dat"
    expected.write_bytes(b"image")
    runner = _runner_for(
        {
            "messages": [
                "[2026-09-28 21:17] Alice: [图片] C:\\wrong\\sample.dat",
            ]
        }
    )

    class Locator:
        def resolve(self, session_id: str, timestamp: str) -> Path | None:
            assert session_id == "room@chatroom"
            assert timestamp == "2026-09-28T21:17:00"
            return expected

    result = WechatCli(
        "fake-wechat-cli",
        runner=runner,
        include_media=True,
        media_locator=Locator(),
    ).history_page("room@chatroom", "2026-09-28", "2026-09-29", limit=50, offset=0)

    assert result[0]["media_path"] == str(expected)
    assert "--media" not in runner.calls[0][0]


def test_media_enabled_history_lazily_uses_current_wechat_resource_locator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = tmp_path / "correct-image.dat"
    expected.write_bytes(b"image")
    runner = _runner_for(
        {"messages": ["[2026-09-28 21:17] Alice: [图片] C:\\wrong\\sample.dat"]}
    )

    class Locator:
        def resolve(self, session_id: str, timestamp: str) -> Path | None:
            return expected

    monkeypatch.setattr(WechatMediaLocator, "from_current_config", lambda: Locator())

    result = WechatCli("fake-wechat-cli", runner=runner, include_media=True).history_page(
        "room@chatroom", "2026-09-28", "2026-09-29", limit=50, offset=0
    )

    assert result[0]["media_path"] == str(expected)


def test_history_page_rejects_current_wechat_cli_envelope_without_messages() -> None:
    runner = _runner_for({"chat": "Research", "username": "room@chatroom"})

    with pytest.raises(DependencyError, match="history"):
        _cli(runner).history_page("room@chatroom", "2026-09-01", "2026-09-07", 10, 0)


def test_constructor_accepts_one_executable_path_not_an_argument_vector() -> None:
    runner = _runner_for([])

    with pytest.raises(TypeError):
        WechatCli(("fake-wechat-cli", "--unexpected"), runner=runner)  # type: ignore[arg-type]


def test_groups_rejects_malformed_output_without_echoing_body() -> None:
    secret = "private chat body"
    runner = RecordingRunner(secret)

    with pytest.raises(DependencyError, match="structured JSON") as error:
        _cli(runner).groups()

    assert secret not in str(error.value)


def test_adapter_rejects_non_list_json_without_echoing_body() -> None:
    secret = "private chat body"
    runner = _runner_for({"message": secret})

    with pytest.raises(DependencyError, match="list") as error:
        _cli(runner).groups()

    assert secret not in str(error.value)


@pytest.mark.parametrize("payload", [["not an object"], [1], [None]])
def test_adapter_rejects_non_object_records(payload: object) -> None:
    runner = _runner_for(payload)

    with pytest.raises(DependencyError, match="JSON object"):
        _cli(runner).groups()


def test_history_rejects_non_object_records() -> None:
    runner = _runner_for(["not an object"])

    with pytest.raises(DependencyError, match="JSON object"):
        _cli(runner).history_page("room-1", "2026-09-01", "2026-09-07", 10, 0)


def test_adapter_rejects_nonzero_exit_without_echoing_message_body() -> None:
    secret = "private stderr body"
    runner = _runner_for([], returncode=7, stderr=secret)

    with pytest.raises(DependencyError, match="exit status") as error:
        _cli(runner).groups()

    assert secret not in str(error.value)


def test_adapter_rejects_missing_executable_without_echoing_path() -> None:
    missing = Path("missing-wechat-cli-for-wx-context-tests")

    def missing_runner(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError(str(missing))

    with pytest.raises(DependencyError, match="not found") as error:
        WechatCli(missing, runner=missing_runner).groups()

    assert str(missing) not in str(error.value)


def test_adapter_rejects_timeout_without_echoing_process_body() -> None:
    def timeout_runner(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(
            cmd=["fake"], timeout=30, output="private stdout body"
        )

    with pytest.raises(DependencyError, match="timed out") as error:
        WechatCli("fake-wechat-cli", runner=timeout_runner).groups()

    assert "private stdout body" not in str(error.value)


def test_adapter_rejects_invalid_utf8_without_echoing_decoded_body() -> None:
    runner = RecordingRunner(b"\xffprivate body")

    with pytest.raises(DependencyError, match="UTF-8") as error:
        _cli(runner).groups()

    assert "private body" not in str(error.value)


@pytest.mark.parametrize("limit", [0, -1, 101, True, "10"])
def test_history_rejects_invalid_limit_before_subprocess(limit: object) -> None:
    runner = _runner_for([])

    with pytest.raises(DependencyError, match="limit"):
        _cli(runner).history_page("room-1", "2026-09-01", "2026-09-07", limit, 0)  # type: ignore[arg-type]

    assert runner.calls == []


@pytest.mark.parametrize("offset", [-1, True, "10"])
def test_history_rejects_invalid_offset_before_subprocess(offset: object) -> None:
    runner = _runner_for([])

    with pytest.raises(DependencyError, match="offset"):
        _cli(runner).history_page("room-1", "2026-09-01", "2026-09-07", 10, offset)  # type: ignore[arg-type]

    assert runner.calls == []


@pytest.mark.parametrize(
    "session_id,start,end",
    [
        ("--room-1", "2026-09-01", "2026-09-07"),
        ("room-1", "--2026-09-01", "2026-09-07"),
        ("room-1", "2026-09-01", "--2026-09-07"),
        ("", "2026-09-01", "2026-09-07"),
    ],
)
def test_history_rejects_option_like_or_blank_arguments_before_subprocess(
    session_id: str, start: str, end: str
) -> None:
    runner = _runner_for([])

    with pytest.raises(DependencyError, match="argument"):
        _cli(runner).history_page(session_id, start, end, 10, 0)

    assert runner.calls == []
