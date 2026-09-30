"""High-level, cross-platform facade for the complete wx-context workflow."""

from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
from typing import Any

from .config import ContextConfig, load_config
from .dependency import WechatCli
from .errors import ValidationError
from .media import MediaResolver
from .models import CollectionManifest, GroupSession
from .service import ContextService


def default_data_dir() -> Path:
    """Return the per-user data directory on Windows, macOS, or Linux."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "wx-context"
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "wx-context"
    return Path.home() / ".local" / "share" / "wx-context"


class WxContextTool:
    """One programmatic entry point for groups, collection, search, and media.

    The class composes the existing allowlist, dependency adapter, storage
    service, and media resolver.  It is intentionally read-only with respect to
    WeChat; the only persistent write is the local allowlist/collection store.
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        dependency: Any | None = None,
        config: ContextConfig | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.config = config if config is not None else load_config(self.data_dir)
        self.dependency = (
            dependency if dependency is not None else WechatCli(include_media=True)
        )
        self.media_resolver = MediaResolver(self.data_dir)
        self.service = ContextService(
            self.config,
            self.dependency,
            media_resolver=self.media_resolver,
        )

    def groups(self) -> list[GroupSession]:
        """List group sessions visible through the read-only dependency."""
        return self.dependency.groups()

    def allow_group(
        self, session_id: str, display_name: str | None = None
    ) -> GroupSession:
        """Allow one exact group session, resolving its name when omitted."""
        if display_name is None:
            for group in self.groups():
                if group.session_id == session_id:
                    display_name = group.display_name
                    break
            if display_name is None:
                raise ValidationError("group session was not found")
        self.config.allow_group(session_id, display_name)
        return GroupSession(session_id.strip(), display_name.strip(), True)

    def collect(
        self,
        session_id: str,
        start: date | str,
        end: date | str,
    ) -> CollectionManifest:
        """Collect one inclusive date range for an allowlisted group."""
        return self.service.collect(
            session_id,
            _coerce_date(start, "start"),
            _coerce_date(end, "end"),
        )

    def search(
        self, query: str, *, session_id: str | None = None, limit: int = 50
    ) -> list[dict[str, object]]:
        """Search only locally stored, still-allowlisted collections."""
        return self.service.search(query, session_id=session_id, limit=limit)

    def context(
        self, collection_id: str, query: str, *, max_chunks: int = 2
    ) -> dict[str, object]:
        """Return bounded chunk pointers for a local query."""
        return self.service.context(collection_id, query, max_chunks=max_chunks)

    @staticmethod
    def preferred_media_path(
        source: str | Path, *, media_dir: str | Path | None = None
    ) -> Path | None:
        """Resolve a media path with high-quality originals before thumbnails."""
        source_path = Path(source)
        resolver = MediaResolver(media_dir or source_path.parent)
        return resolver.preferred_path(source_path)


def _coerce_date(value: date | str, name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, (date, str)):
        raise ValidationError(f"{name} date must be ISO format YYYY-MM-DD")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError(f"{name} date must be ISO format YYYY-MM-DD") from error
