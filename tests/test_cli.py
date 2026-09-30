from __future__ import annotations

import json
import io
import os
from pathlib import Path

import pytest

from wx_context import cli
from wx_context.config import load_config
from wx_context.errors import ValidationError
from wx_context.models import GroupSession


def _write_collection(
    root: Path,
    collection_id: str,
    session_id: str,
    records: list[dict[str, object]],
) -> None:
    collection = root / "collections" / collection_id
    chunks = collection / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "chunk-0001.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    (collection / "manifest.json").write_text(
        json.dumps(
            {
                "collection_id": collection_id,
                "session_id": session_id,
                "display_name": "Research",
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
                "message_count": len(records),
                "chunk_count": 1,
                "created_at": "2026-09-01T00:00:00+00:00",
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    (collection / "index.json").write_text(
        json.dumps(
            {
                "collection_id": collection_id,
                "schema_version": 1,
                "chunks": [
                    {
                        "path": "chunks/chunk-0001.jsonl",
                        "message_count": len(records),
                        "start_timestamp": records[0]["timestamp"],
                        "end_timestamp": records[-1]["timestamp"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _record(number: int, text: str = "deadline") -> dict[str, object]:
    return {
        "id": f"message-{number}",
        "timestamp": f"2026-09-01T00:00:{number:02d}Z",
        "sender": "Alice",
        "text": text,
        "type": "text",
    }


def test_context_limits_chunk_manifest_and_never_prints_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = load_config(tmp_path)
    config.allow_group("room-1", "Research")
    _write_collection(tmp_path, "col-1", "room-1", [_record(1)])

    assert (
        cli.main(
            [
                "--data-dir",
                str(tmp_path),
                "context",
                "col-1",
                "--query",
                "deadline",
                "--max-chunks",
                "1",
            ]
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["query"] == "deadline"
    assert len(result["chunks"]) == 1
    assert "text" not in json.dumps(result)


def test_cli_routes_group_listing_through_high_level_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[Path] = []

    class FakeTool:
        def __init__(self, data_dir: Path, **_: object) -> None:
            calls.append(data_dir)

        def groups(self) -> list[GroupSession]:
            return [GroupSession("room-1", "Research", True)]

    monkeypatch.setattr(cli, "WxContextTool", FakeTool, raising=False)

    assert cli.main(["--data-dir", str(tmp_path), "groups"]) == 0
    assert calls == [tmp_path]
    assert json.loads(capsys.readouterr().out) == {
        "groups": [
            {"session_id": "room-1", "display_name": "Research", "is_group": True}
        ]
    }


def test_json_output_requests_utf8_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    class RecordingText(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.reconfigure_calls: list[dict[str, object]] = []

        def reconfigure(self, **kwargs: object) -> None:
            self.reconfigure_calls.append(kwargs)

    stream = RecordingText()
    monkeypatch.setattr(cli.sys, "stdout", stream)

    cli._write_json({"display_name": "研究群"})

    assert stream.reconfigure_calls == [{"encoding": "utf-8", "errors": "strict"}]
    assert json.loads(stream.getvalue()) == {"display_name": "研究群"}


def test_search_is_bounded_and_does_not_scan_unallowlisted_collections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = load_config(tmp_path)
    config.allow_group("room-1", "Research")
    _write_collection(tmp_path, "allowed", "room-1", [_record(1) for _ in range(3)])
    _write_collection(
        tmp_path,
        "secret",
        "room-secret",
        [_record(1, "deadline secret")],
    )

    assert (
        cli.main(["--data-dir", str(tmp_path), "search", "deadline", "--limit", "999"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert len(result["results"]) == 3
    assert all(item["session_id"] == "room-1" for item in result["results"])


def test_known_errors_are_redacted_json_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise ValidationError("sensitive path and message")

    monkeypatch.setattr(cli, "load_config", fail)
    assert cli.main(["--data-dir", str(tmp_path), "groups"]) == 2

    captured = capsys.readouterr()
    assert json.loads(captured.err) == {
        "error": "validation_error",
        "message": "invalid request",
    }
    assert "sensitive" not in captured.err


def test_unsupported_send_command_is_rejected() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["send"])
    assert error.value.code == 2


def test_default_data_dir_is_per_user_and_outside_source_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if os.name == "nt":
        data_home = tmp_path / "AppData" / "Local"
        monkeypatch.setenv("LOCALAPPDATA", str(data_home))
        monkeypatch.delenv("APPDATA", raising=False)
    else:
        data_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    data_dir = cli.default_data_dir()
    source_tree = Path(__file__).resolve().parents[1]

    assert data_dir == data_home / "wx-context"
    assert source_tree not in data_dir.parents
