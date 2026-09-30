"""Strictly read-only adapter for the separately installed ``wechat-cli``."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from .errors import DependencyError
from .media_index import WechatMediaLocator
from .models import GroupSession


Runner = Callable[..., subprocess.CompletedProcess[Any]]
_HISTORY_LINE_RE = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\]\s*(?P<body>.*)$",
    re.DOTALL,
)
MAX_HISTORY_PAGE_SIZE = 100


class WechatCli:
    """Invoke only the allowlisted, read-only commands exposed by ``wechat-cli``."""

    def __init__(
        self,
        executable: str | os.PathLike[str] = "wechat-cli",
        *,
        runner: Runner | None = None,
        include_media: bool = False,
        media_locator: Any | None = None,
    ) -> None:
        try:
            executable_path = os.fspath(executable)
        except TypeError:
            raise TypeError("executable must be one string or path") from None
        if not isinstance(executable_path, str):
            raise TypeError("executable must be one string or path")
        if not executable_path:
            raise ValueError("executable must not be blank")
        self._executable = executable_path
        self._runner = subprocess.run if runner is None else runner
        if not isinstance(include_media, bool):
            raise TypeError("include_media must be a boolean")
        self._include_media = include_media
        self._media_locator = media_locator
        self._attempted_default_media_locator = media_locator is not None

    def groups(self) -> list[GroupSession]:
        """Return only sessions identified by the dependency as group chats."""
        payload = self._run_json(["sessions"])
        groups: list[GroupSession] = []
        for item in payload:
            marker = item.get("is_group")
            if not isinstance(marker, bool):
                raise DependencyError("wechat-cli returned an invalid group marker")
            if marker is not True:
                continue
            session_id = item.get("session_id", item.get("username"))
            display_name = item.get("display_name", item.get("chat"))
            if not isinstance(session_id, str) or not isinstance(display_name, str):
                raise DependencyError("wechat-cli returned an invalid group session")
            groups.append(
                GroupSession(
                    session_id=session_id,
                    display_name=display_name,
                    is_group=True,
                )
            )
        return groups

    def history_page(
        self,
        session_id: str,
        start: str,
        end: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Return one bounded history page for a session and date range."""
        self._validate_argument(session_id, "session id")
        self._validate_argument(start, "start time")
        self._validate_argument(end, "end time")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_HISTORY_PAGE_SIZE
        ):
            raise DependencyError(
                f"history limit must be an integer from 1 through {MAX_HISTORY_PAGE_SIZE}"
            )
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise DependencyError("history offset must be a nonnegative integer")
        arguments = [
            "history",
            session_id,
            "--start-time",
            start,
            "--end-time",
            end,
            "--limit",
            str(limit),
            "--offset",
            str(offset),
        ]
        # The external CLI's --media mode scans the attachment tree once per
        # image.  When the message-resource index is available, it provides a
        # precise path without that repeated scan, so reserve --media for the
        # fallback path only.
        locator = self._active_media_locator()
        if self._include_media and locator is None:
            arguments.append("--media")
        payload = self._run_payload(arguments)
        if isinstance(payload, list):
            self._validate_records(payload)
            records = cast(list[dict[str, Any]], payload)
            return self._replace_media_paths(session_id, records, locator)
        if isinstance(payload, dict):
            messages = payload.get("messages")
            if not isinstance(messages, list):
                raise DependencyError("wechat-cli returned a history payload without messages")
            if any(not isinstance(item, (dict, str)) for item in messages):
                raise DependencyError("wechat-cli returned a history message that was not an object or string")
            records = [
                item if isinstance(item, dict) else _parse_history_line(item)
                for item in messages
            ]
            return self._replace_media_paths(session_id, records, locator)
        raise DependencyError("wechat-cli returned a history payload that was not a list or object")

    def _replace_media_paths(
        self,
        session_id: str,
        records: list[dict[str, Any]],
        locator: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Correct ``wechat-cli --media`` sample paths when an index is supplied."""
        if locator is None:
            return records
        for record in records:
            if record.get("type") != "image":
                continue
            timestamp = record.get("timestamp")
            if not isinstance(timestamp, str):
                continue
            resolved = locator.resolve(session_id, timestamp)
            if isinstance(resolved, Path) and resolved.is_file():
                record["media_path"] = str(resolved)
        return records

    def _active_media_locator(self) -> Any | None:
        if not self._include_media:
            return None
        if self._media_locator is not None:
            return self._media_locator
        if self._attempted_default_media_locator:
            return None
        self._attempted_default_media_locator = True
        try:
            self._media_locator = WechatMediaLocator.from_current_config()
        except Exception:
            return None
        return self._media_locator

    @staticmethod
    def _validate_argument(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip() or value.startswith("-"):
            raise DependencyError(f"invalid {name} argument")

    def _run_json(self, arguments: list[str]) -> list[dict[str, Any]]:
        payload = self._run_payload(arguments)
        if not isinstance(payload, list):
            raise DependencyError("wechat-cli returned structured JSON that was not a list")
        self._validate_records(payload)
        return cast(list[dict[str, Any]], payload)

    @staticmethod
    def _validate_records(payload: list[object]) -> None:
        if any(not isinstance(item, dict) for item in payload):
            raise DependencyError("wechat-cli returned a list containing a non-JSON object")

    def _run_payload(self, arguments: list[str]) -> object:
        child_environment = os.environ.copy()
        child_environment["PYTHONIOENCODING"] = "utf-8"
        child_environment["PYTHONUTF8"] = "1"
        try:
            result = self._runner(
                [self._executable, *arguments],
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=30,
                check=False,
                env=child_environment,
            )
        except FileNotFoundError:
            raise DependencyError("wechat-cli executable was not found") from None
        except subprocess.TimeoutExpired:
            raise DependencyError("wechat-cli command timed out") from None
        except UnicodeDecodeError:
            raise DependencyError("wechat-cli output was not valid UTF-8") from None
        except OSError:
            raise DependencyError("wechat-cli executable could not be run") from None

        if result.returncode != 0:
            raise DependencyError("wechat-cli command returned a nonzero exit status")

        try:
            stdout = result.stdout
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8")
            payload = json.loads(stdout)
        except UnicodeDecodeError:
            raise DependencyError("wechat-cli output was not valid UTF-8") from None
        except (TypeError, json.JSONDecodeError):
            raise DependencyError("wechat-cli did not return structured JSON") from None
        return payload


def _parse_history_line(line: str) -> dict[str, str]:
    match = _HISTORY_LINE_RE.match(line)
    if match is None:
        raise DependencyError("wechat-cli returned a history message without a timestamp")
    timestamp = match.group("timestamp").replace(" ", "T")
    if len(timestamp) == 16:
        timestamp += ":00"
    body = match.group("body")
    sender, separator, text = body.partition(": ")
    if not separator:
        sender, text = "", body
    if text.startswith("[图片]"):
        media_path = text[len("[图片]") :].strip()
        result = {
            "timestamp": timestamp,
            "sender": sender,
            "text": "[图片]",
            "type": "image",
        }
        if media_path:
            result["media_path"] = media_path
        return result
    return {
        "timestamp": timestamp,
        "sender": sender,
        "text": text,
        "type": "text",
    }
