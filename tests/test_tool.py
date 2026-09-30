from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from wx_context.models import GroupSession
from wx_context.service import HISTORY_PAGE_SIZE
from wx_context.tool import WxContextTool


class FakeDependency:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def groups(self) -> list[GroupSession]:
        self.calls.append(("groups",))
        return [GroupSession("room-1", "Research", True)]

    def history_page(
        self, session_id: str, start: str, end: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        self.calls.append(("history", session_id, start, end, str(limit), str(offset)))
        return [
            {
                "id": "message-1",
                "timestamp": "2026-09-01T10:00:00",
                "sender": "Alice",
                "text": "hello",
                "type": "text",
            }
        ]


def test_tool_exposes_group_allow_and_bounded_collect_as_one_api(
    tmp_path: Path,
) -> None:
    dependency = FakeDependency()
    tool = WxContextTool(tmp_path, dependency=dependency)

    assert tool.groups() == [GroupSession("room-1", "Research", True)]
    assert tool.allow_group("room-1") == GroupSession("room-1", "Research", True)

    manifest = tool.collect("room-1", "2026-09-01", date(2026, 9, 1))

    assert manifest.message_count == 1
    assert dependency.calls[-1] == (
        "history",
        "room-1",
        "2026-09-01",
        "2026-09-01",
        str(HISTORY_PAGE_SIZE),
        "0",
    )


def test_tool_delegates_local_search_and_context(tmp_path: Path) -> None:
    dependency = FakeDependency()
    tool = WxContextTool(tmp_path, dependency=dependency)
    tool.allow_group("room-1", "Research")
    tool.collect("room-1", "2026-09-01", "2026-09-01")

    assert tool.search("hello")
    result = tool.context(tool.collect("room-1", "2026-09-01", "2026-09-01").collection_id, "hello")
    assert result["query"] == "hello"
