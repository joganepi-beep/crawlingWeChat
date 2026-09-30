"""Small, serializable domain models used by wx-context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class GroupSession:
    """A session exposed by the dependency that represents a group chat."""

    session_id: str
    display_name: str
    is_group: bool


@dataclass(frozen=True)
class CollectionManifest:
    """Metadata for one locally collected, date-bounded message history."""

    collection_id: str
    session_id: str
    display_name: str
    start_date: date
    end_date: date
    message_count: int
    chunk_count: int
    created_at: datetime
    schema_version: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation of this manifest."""
        return {
            "collection_id": self.collection_id,
            "session_id": self.session_id,
            "display_name": self.display_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "message_count": self.message_count,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at.isoformat(),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "CollectionManifest":
        """Build a manifest from its JSON-compatible representation."""
        return cls(
            collection_id=str(value["collection_id"]),
            session_id=str(value["session_id"]),
            display_name=str(value["display_name"]),
            start_date=date.fromisoformat(str(value["start_date"])),
            end_date=date.fromisoformat(str(value["end_date"])),
            message_count=int(value["message_count"]),
            chunk_count=int(value["chunk_count"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            schema_version=int(value["schema_version"]),
        )
