from __future__ import annotations

import json
from datetime import date, datetime, timedelta
import hashlib
from pathlib import Path

import pytest

from wx_context.config import load_config
from wx_context.errors import DependencyError, ValidationError
from wx_context.media import MediaResolver
from wx_context.service import HISTORY_PAGE_SIZE, ContextService


class FakeHistory:
    def __init__(self, pages: list[list[dict[str, object]] | Exception]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, str, int, int]] = []

    def history_page(
        self, session_id: str, start: str, end: str, limit: int, offset: int
    ) -> list[dict[str, object]]:
        self.calls.append((session_id, start, end, limit, offset))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def _service(tmp_path: Path, dependency: FakeHistory) -> ContextService:
    config = load_config(tmp_path)
    config.allow_group("room-1", "Research")
    return ContextService(config, dependency)


def _record(number: int, *, timestamp: str | None = None) -> dict[str, object]:
    created_at = datetime(2026, 9, 1) + timedelta(seconds=number)
    return {
        "id": f"message-{number}",
        "timestamp": timestamp or created_at.isoformat(timespec="seconds"),
        "sender": "Alice",
        "text": f"message {number}",
        "type": "text",
    }


def test_collect_requires_allowlisted_group(tmp_path: Path) -> None:
    dependency = FakeHistory([[]])
    service = ContextService(load_config(tmp_path), dependency)

    with pytest.raises(ValidationError, match="allowlisted"):
        service.collect("room-1", date(2026, 9, 1), date(2026, 9, 2))

    assert dependency.calls == []


def test_collect_rejects_reversed_dates(tmp_path: Path) -> None:
    dependency = FakeHistory([[]])
    service = _service(tmp_path, dependency)

    with pytest.raises(ValidationError, match="on or after"):
        service.collect("room-1", date(2026, 9, 2), date(2026, 9, 1))

    assert dependency.calls == []


def test_collect_deduplicates_and_chunks_chronologically(tmp_path: Path) -> None:
    records = [_record(number) for number in range(201)]
    records.append(dict(records[100]))
    dependency = FakeHistory([records])
    service = _service(tmp_path, dependency)

    manifest = service.collect("room-1", date(2026, 9, 1), date(2026, 9, 2))

    assert (manifest.message_count, manifest.chunk_count) == (201, 2)
    collection = tmp_path / "collections" / manifest.collection_id
    assert collection.is_dir()
    assert not list((tmp_path / "collections").glob(".tmp-*"))
    assert json.loads((collection / "manifest.json").read_text(encoding="utf-8"))[
        "message_count"
    ] == 201
    index = json.loads((collection / "index.json").read_text(encoding="utf-8"))
    assert [item["message_count"] for item in index["chunks"]] == [200, 1]

    chunks = sorted((collection / "chunks").glob("*.jsonl"))
    lines = [json.loads(line) for chunk in chunks for line in chunk.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 201
    assert list(lines[0]) == ["id", "timestamp", "sender", "text", "type"]
    assert [item["timestamp"] for item in lines] == sorted(item["timestamp"] for item in lines)


def test_collect_stores_at_most_one_bounded_page(tmp_path: Path) -> None:
    dependency = FakeHistory([[_record(number) for number in range(HISTORY_PAGE_SIZE)]])
    service = _service(tmp_path, dependency)

    manifest = service.collect("room-1", date(2026, 9, 1), date(2026, 9, 2))

    assert manifest.message_count == HISTORY_PAGE_SIZE
    assert dependency.calls == [
        ("room-1", "2026-09-01", "2026-09-02", HISTORY_PAGE_SIZE, 0),
    ]


def test_collect_deduplicates_records_without_source_id_by_canonical_hash(
    tmp_path: Path,
) -> None:
    record = {
        "timestamp": "2026-09-01T00:00:00",
        "sender": "Alice",
        "text": "same message",
        "type": "text",
    }
    dependency = FakeHistory([[record, dict(record)]])
    service = _service(tmp_path, dependency)

    manifest = service.collect("room-1", date(2026, 9, 1), date(2026, 9, 1))

    assert manifest.message_count == 1
    chunk = next((tmp_path / "collections" / manifest.collection_id / "chunks").glob("*.jsonl"))
    stored = json.loads(chunk.read_text(encoding="utf-8"))
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert stored["id"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_collect_accepts_mixed_naive_and_aware_timestamps(tmp_path: Path) -> None:
    records = [
        _record(1, timestamp="2026-09-01T00:00:00"),
        _record(2, timestamp="2026-09-01T00:00:01Z"),
    ]
    dependency = FakeHistory([records])
    service = _service(tmp_path, dependency)

    manifest = service.collect("room-1", date(2026, 9, 1), date(2026, 9, 1))

    assert manifest.message_count == 2


def test_collect_stores_original_media_path_before_thumbnail(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    thumbnail = media_dir / "image_t.dat"
    original = media_dir / "image_h.dat"
    thumbnail.write_bytes(b"thumbnail")
    original.write_bytes(b"original")
    dependency = FakeHistory(
        [[
            {
                **_record(1),
                "type": "image",
                "media_path": str(thumbnail),
            }
        ]]
    )
    config = load_config(tmp_path)
    config.allow_group("room-1", "Research")
    service = ContextService(config, dependency, media_resolver=MediaResolver(media_dir))

    manifest = service.collect("room-1", date(2026, 9, 1), date(2026, 9, 1))

    chunk = next((tmp_path / "collections" / manifest.collection_id / "chunks").glob("*.jsonl"))
    stored = json.loads(chunk.read_text(encoding="utf-8"))
    assert stored["media_path"] == str(original)


def test_collect_rejects_records_without_parseable_timestamp(tmp_path: Path) -> None:
    dependency = FakeHistory([[_record(1, timestamp="not-a-timestamp")]])
    service = _service(tmp_path, dependency)

    with pytest.raises(ValidationError, match="timestamp"):
        service.collect("room-1", date(2026, 9, 1), date(2026, 9, 2))

    assert not list((tmp_path / "collections").glob("*"))


def test_dependency_failure_leaves_no_published_collection(tmp_path: Path) -> None:
    dependency = FakeHistory([DependencyError("dependency unavailable")])
    service = _service(tmp_path, dependency)
    collections_root = tmp_path / "collections"

    with pytest.raises(DependencyError, match="dependency unavailable"):
        service.collect("room-1", date(2026, 9, 1), date(2026, 9, 1))

    assert not list(collections_root.iterdir())
