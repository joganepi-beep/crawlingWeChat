"""Validated collection service for explicitly allowlisted group history."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .config import ContextConfig
from .errors import DependencyError, StorageError, ValidationError
from .media import MediaResolver
from .models import CollectionManifest
from .storage import write_collection_atomic


HISTORY_PAGE_SIZE = 100


class HistoryDependency(Protocol):
    def history_page(
        self, session_id: str, start: str, end: str, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...


class ContextService:
    """Collect bounded, normalized history through a read-only dependency."""

    def __init__(
        self,
        config: ContextConfig,
        dependency: HistoryDependency,
        *,
        media_resolver: MediaResolver | None = None,
    ) -> None:
        self.config = config
        self.dependency = dependency
        self.media_resolver = media_resolver

    def collect(self, session_id: str, start: date, end: date) -> CollectionManifest:
        """Collect at most the latest bounded page and atomically publish it."""
        if not isinstance(start, date) or isinstance(start, datetime):
            raise ValidationError("start date must be a date")
        if not isinstance(end, date) or isinstance(end, datetime):
            raise ValidationError("end date must be a date")
        if end < start:
            raise ValidationError("end date must be on or after start date")
        if session_id not in self.config.allowed_groups:
            raise ValidationError(f"group is not allowlisted: {session_id}")

        collections_root = self.config.data_dir / "collections"
        try:
            collections_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StorageError("could not prepare collection storage") from error

        start_value, end_value = start.isoformat(), end.isoformat()
        messages: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        page = self.dependency.history_page(
            session_id, start_value, end_value, HISTORY_PAGE_SIZE, 0
        )
        if not isinstance(page, list):
            raise DependencyError("dependency returned an invalid history page")
        for record in page:
            normalized, key = _normalize_record(record, self.media_resolver)
            if key not in seen:
                seen.add(key)
                messages.append(normalized)

        messages.sort(key=lambda message: _timestamp_sort_key(message["timestamp"]))
        manifest = CollectionManifest(
            collection_id=uuid4().hex,
            session_id=session_id,
            display_name=self.config.allowed_groups[session_id],
            start_date=start,
            end_date=end,
            message_count=len(messages),
            chunk_count=(len(messages) + 199) // 200,
            created_at=datetime.now(timezone.utc),
            schema_version=1,
        )
        write_collection_atomic(collections_root, manifest, messages)
        return manifest

    def search(
        self, query: str, *, session_id: str | None = None, limit: int = 50
    ) -> list[dict[str, object]]:
        """Search locally stored chunks for an allowlisted session.

        Search never invokes the dependency.  It reads only collection directories
        whose manifest session is still present in the current allowlist.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("search query must not be blank")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValidationError("search limit must be a positive integer")
        limit = min(limit, 200)
        if session_id is not None and session_id not in self.config.allowed_groups:
            raise ValidationError(f"group is not allowlisted: {session_id}")

        results: list[dict[str, object]] = []
        needle = query.casefold()
        for collection_dir in self._collection_directories():
            manifest, index = self._load_collection_metadata(collection_dir)
            collection_session = manifest.session_id
            if collection_session not in self.config.allowed_groups:
                continue
            if session_id is not None and collection_session != session_id:
                continue
            for chunk in index["chunks"]:
                chunk_path = self._chunk_path(collection_dir, chunk["path"])
                for record in self._read_chunk(chunk_path):
                    searchable = json.dumps(record, ensure_ascii=False).casefold()
                    if needle not in searchable:
                        continue
                    results.append(
                        {
                            "collection_id": manifest.collection_id,
                            "session_id": collection_session,
                            "display_name": manifest.display_name,
                            "chunk_path": chunk["path"],
                            **record,
                        }
                    )
                    if len(results) >= limit:
                        return results
        return results

    def context(
        self, collection_id: str, query: str, *, max_chunks: int = 2
    ) -> dict[str, object]:
        """Return bounded metadata pointers for chunks relevant to ``query``."""
        if not isinstance(collection_id, str) or not collection_id.strip():
            raise ValidationError("collection id must not be blank")
        if Path(collection_id).name != collection_id or collection_id in {".", ".."}:
            raise ValidationError("invalid collection id")
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("context query must not be blank")
        if isinstance(max_chunks, bool) or not isinstance(max_chunks, int) or max_chunks < 1:
            raise ValidationError("max chunks must be a positive integer")
        max_chunks = min(max_chunks, 200)

        collection_dir = self.config.data_dir / "collections" / collection_id
        if not collection_dir.is_dir():
            raise StorageError("collection was not found")
        manifest, index = self._load_collection_metadata(collection_dir)
        if manifest.session_id not in self.config.allowed_groups:
            raise ValidationError(f"group is not allowlisted: {manifest.session_id}")

        needle = query.casefold()
        selected: list[dict[str, object]] = []
        for chunk in index["chunks"]:
            chunk_path = self._chunk_path(collection_dir, chunk["path"])
            records = self._read_chunk(chunk_path)
            if any(needle in json.dumps(record, ensure_ascii=False).casefold() for record in records):
                selected.append(
                    {
                        "path": chunk["path"],
                        "message_count": chunk["message_count"],
                        "start_timestamp": chunk["start_timestamp"],
                        "end_timestamp": chunk["end_timestamp"],
                    }
                )
                if len(selected) >= max_chunks:
                    break
        return {
            "collection_id": manifest.collection_id,
            "session_id": manifest.session_id,
            "display_name": manifest.display_name,
            "query": query,
            "chunks": selected,
        }

    def _collection_directories(self) -> list[Path]:
        root = self.config.data_dir / "collections"
        try:
            if not root.exists():
                return []
            return sorted(item for item in root.iterdir() if item.is_dir())
        except OSError as error:
            raise StorageError("could not read collection storage") from error

    def _load_collection_metadata(
        self, collection_dir: Path
    ) -> tuple[CollectionManifest, dict[str, list[dict[str, object]]]]:
        try:
            manifest_payload = json.loads(
                (collection_dir / "manifest.json").read_text(encoding="utf-8")
            )
            if not isinstance(manifest_payload, dict):
                raise ValueError("manifest must be an object")
            manifest = CollectionManifest.from_dict(manifest_payload)
            if manifest.collection_id != collection_dir.name:
                raise ValueError("manifest collection id does not match directory")
            index = json.loads((collection_dir / "index.json").read_text(encoding="utf-8"))
            if (
                not isinstance(index, dict)
                or index.get("collection_id") != manifest.collection_id
                or index.get("schema_version") != 1
                or not isinstance(index.get("chunks"), list)
            ):
                raise ValueError("invalid collection index")
            chunks = index["chunks"]
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    raise ValueError("invalid collection chunk index")
                for key in ("path", "message_count", "start_timestamp", "end_timestamp"):
                    if key not in chunk:
                        raise ValueError("invalid collection chunk index")
                if not isinstance(chunk["path"], str) or not isinstance(
                    chunk["message_count"], int
                ):
                    raise ValueError("invalid collection chunk index")
            return manifest, {"chunks": chunks}
        except StorageError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StorageError("could not read collection metadata") from error

    @staticmethod
    def _chunk_path(collection_dir: Path, relative_path: object) -> Path:
        if not isinstance(relative_path, str):
            raise StorageError("invalid collection chunk path")
        relative = Path(relative_path)
        chunks_dir = collection_dir / "chunks"
        if relative.parent != Path("chunks") or len(relative.parts) != 2:
            raise StorageError("invalid collection chunk path")
        path = collection_dir / relative
        if path.parent != chunks_dir:
            raise StorageError("invalid collection chunk path")
        return path

    @staticmethod
    def _read_chunk(path: Path) -> list[dict[str, object]]:
        try:
            records: list[dict[str, object]] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("chunk record must be an object")
                records.append(value)
            return records
        except StorageError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StorageError("could not read collection chunk") from error


def _normalize_record(
    record: object, media_resolver: MediaResolver | None = None
) -> tuple[dict[str, object], tuple[str, str]]:
    if not isinstance(record, Mapping):
        raise ValidationError("history record must be an object")
    raw_timestamp = _first(record, "timestamp", "time", "created_at", "datetime")
    _parse_timestamp(raw_timestamp)
    timestamp = raw_timestamp.strip() if isinstance(raw_timestamp, str) else ""

    source_id = _first(record, "id", "source_id", "message_id")
    if source_id is not None and str(source_id):
        identity = str(source_id)
        dedup_key = ("source-id", identity)
    else:
        try:
            canonical = json.dumps(
                dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as error:
            raise ValidationError("history record is not canonical JSON") from error
        identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        dedup_key = ("content-hash", identity)

    normalized = {
        "id": identity,
        "timestamp": timestamp,
        "sender": _string_value(_first(record, "sender", "from", "sender_name")),
        "text": _string_value(_first(record, "text", "content", "body")),
        "type": _string_value(_first(record, "type", "message_type")),
    }
    raw_media_path = _media_path(record)
    if raw_media_path:
        resolved = (
            media_resolver.preferred_path(raw_media_path)
            if media_resolver is not None
            else None
        )
        normalized["media_path"] = str(resolved or raw_media_path)
    return normalized, dedup_key


def _media_path(record: Mapping[str, object]) -> str:
    value = _first(record, "media_path", "local_path")
    if value is None:
        media = record.get("media")
        if isinstance(media, Mapping):
            value = _first(media, "media_path", "local_path", "path")
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _first(record: Mapping[str, object], *names: str) -> object:
    for name in names:
        if name in record:
            return record[name]
    return None


def _string_value(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("history record is missing a parseable timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError("history record is missing a parseable timestamp") from error
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_sort_key(value: object) -> datetime:
    return _parse_timestamp(value)
