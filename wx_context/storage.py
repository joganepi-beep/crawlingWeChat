"""Atomic, bounded-on-disk storage for collected message histories."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from .errors import StorageError
from .models import CollectionManifest


CHUNK_SIZE = 200
SCHEMA_VERSION = 1


def write_collection_atomic(
    collections_root: Path | str,
    manifest: CollectionManifest,
    messages: Iterable[dict[str, object]],
) -> Path:
    """Write and validate one collection, then publish it with one rename."""
    root = Path(collections_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root, prefix=".tmp-") as temporary_name:
            working = Path(temporary_name)
            chunks_directory = working / "chunks"
            chunks_directory.mkdir()
            message_list = list(messages)
            chunk_entries = _write_chunks(chunks_directory, message_list)
            _write_json(working / "manifest.json", manifest.to_dict())
            _write_json(
                working / "index.json",
                {
                    "collection_id": manifest.collection_id,
                    "schema_version": SCHEMA_VERSION,
                    "chunks": chunk_entries,
                },
            )
            _validate_collection(working, manifest)

            final_directory = root / manifest.collection_id
            if final_directory.exists():
                raise StorageError("collection already exists")
            os.replace(working, final_directory)
            return final_directory
    except StorageError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise StorageError("could not write collection") from error


def _write_chunks(directory: Path, messages: list[dict[str, object]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for offset in range(0, len(messages), CHUNK_SIZE):
        chunk = messages[offset : offset + CHUNK_SIZE]
        filename = f"chunk-{offset // CHUNK_SIZE + 1:04d}.jsonl"
        path = directory / filename
        try:
            with path.open("w", encoding="utf-8", newline="\n") as output:
                for message in chunk:
                    output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
                    output.write("\n")
                output.flush()
                os.fsync(output.fileno())
        except (OSError, TypeError, ValueError) as error:
            raise StorageError("could not write collection chunk") from error
        entries.append(
            {
                "path": f"chunks/{filename}",
                "message_count": len(chunk),
                "start_timestamp": chunk[0]["timestamp"],
                "end_timestamp": chunk[-1]["timestamp"],
            }
        )
    return entries


def _write_json(path: Path, payload: dict[str, object]) -> None:
    try:
        with path.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except (OSError, TypeError, ValueError) as error:
        raise StorageError("could not write collection metadata") from error


def _validate_collection(working: Path, manifest: CollectionManifest) -> None:
    """Validate metadata and every JSONL record before publication."""
    try:
        manifest_payload = json.loads((working / "manifest.json").read_text(encoding="utf-8"))
        loaded_manifest = CollectionManifest.from_dict(manifest_payload)
        if loaded_manifest != manifest:
            raise ValueError("manifest does not match collected messages")
        index = json.loads((working / "index.json").read_text(encoding="utf-8"))
        if (
            index.get("collection_id") != manifest.collection_id
            or index.get("schema_version") != SCHEMA_VERSION
            or not isinstance(index.get("chunks"), list)
        ):
            raise ValueError("invalid collection index")

        total = 0
        previous_timestamp: datetime | None = None
        for entry in index["chunks"]:
            if not isinstance(entry, dict):
                raise ValueError("invalid collection chunk index")
            relative_path = entry.get("path")
            if not isinstance(relative_path, str) or not relative_path.startswith("chunks/"):
                raise ValueError("invalid collection chunk path")
            chunk_path = working / relative_path
            if chunk_path.parent != working / "chunks":
                raise ValueError("invalid collection chunk path")
            records = [
                json.loads(line)
                for line in chunk_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            if len(records) != entry.get("message_count") or len(records) > CHUNK_SIZE:
                raise ValueError("invalid collection chunk count")
            if records:
                if entry.get("start_timestamp") != records[0].get("timestamp"):
                    raise ValueError("invalid collection chunk start")
                if entry.get("end_timestamp") != records[-1].get("timestamp"):
                    raise ValueError("invalid collection chunk end")
            for record in records:
                required_fields = {"id", "timestamp", "sender", "text", "type"}
                optional_fields = {"media_path"}
                if (
                    not isinstance(record, dict)
                    or not required_fields.issubset(record)
                    or set(record) - required_fields - optional_fields
                    or (
                        "media_path" in record
                        and not isinstance(record["media_path"], str)
                    )
                ):
                    raise ValueError("invalid normalized message")
                timestamp = _parse_timestamp(record["timestamp"])
                if previous_timestamp is not None and timestamp < previous_timestamp:
                    raise ValueError("messages are not chronological")
                previous_timestamp = timestamp
            total += len(records)
        if total != manifest.message_count or len(index["chunks"]) != manifest.chunk_count:
            raise ValueError("collection metadata counts do not match")
    except StorageError:
        raise
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise StorageError("collection validation failed") from error


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)
