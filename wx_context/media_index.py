"""Message-specific WeChat media lookup from the local resource index."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .media import MediaResolver


_RESOURCE_MD5_RE = re.compile(rb"[0-9a-f]{32}")


class WechatMediaLocator:
    """Resolve an image message to its own locally cached resource file.

    ``wechat-cli --media`` currently supplies one sample image path for all
    images in a month. This index uses the message-resource database instead,
    so the message timestamp selects the correct resource MD5 before the usual
    original-first variant selection is applied.
    """

    def __init__(self, resource_db: str | Path, attachment_root: str | Path) -> None:
        self.resource_db = Path(resource_db)
        self.attachment_root = Path(attachment_root)
        self._minute_positions: dict[tuple[str, int], int] = {}
        self._minute_md5s: dict[tuple[str, int], tuple[str, ...]] = {}

    @classmethod
    def from_current_config(cls) -> "WechatMediaLocator":
        """Open the read-only resource index configured for ``wechat-cli``."""
        from wechat_cli.core.config import load_config
        from wechat_cli.core.db_cache import DBCache

        config = load_config()
        db_dir = Path(config["db_dir"])
        all_keys = json.loads(Path(config["keys_file"]).read_text(encoding="utf-8"))
        cache = DBCache(all_keys, str(db_dir))
        resource_db = cache.get("message/message_resource.db")
        return cls(resource_db, db_dir.parent / "msg" / "attach")

    def resolve(self, session_id: str, timestamp: str) -> Path | None:
        moment = datetime.fromisoformat(timestamp)
        minute_start = int(moment.timestamp())
        minute_key = (session_id, minute_start)
        resource_md5s = self._resource_md5s_for_minute(session_id, minute_start)
        position = self._minute_positions.get(minute_key, 0)
        if position >= len(resource_md5s):
            return None
        self._minute_positions[minute_key] = position + 1
        resource_md5 = resource_md5s[position]
        image_dir = (
            self.attachment_root
            / hashlib.md5(session_id.encode()).hexdigest()
            / moment.strftime("%Y-%m")
            / "Img"
        )
        return MediaResolver(image_dir).preferred_path(image_dir / f"{resource_md5}.dat")

    def _resource_md5s_for_minute(
        self, session_id: str, minute_start: int
    ) -> tuple[str, ...]:
        key = (session_id, minute_start)
        cached = self._minute_md5s.get(key)
        if cached is not None:
            return cached
        result = tuple(self._resource_md5s(session_id, minute_start))
        self._minute_md5s[key] = result
        return result

    def _resource_md5s(self, session_id: str, minute_start: int) -> list[str]:
        query = """
            SELECT resource.packed_info
            FROM MessageResourceInfo AS resource
            WHERE resource.chat_id = (
                SELECT rowid FROM ChatName2Id WHERE user_name = ? LIMIT 1
            )
              AND (resource.message_local_type & 4294967295) = 3
              AND resource.message_create_time >= ?
              AND resource.message_create_time < ?
            ORDER BY resource.message_create_time, resource.message_local_id
        """
        with sqlite3.connect(self.resource_db) as connection:
            rows = connection.execute(
                query, (session_id, minute_start, minute_start + 60)
            ).fetchall()
        resource_md5s: list[str] = []
        for (packed_info,) in rows:
            if not isinstance(packed_info, bytes):
                continue
            match = _RESOURCE_MD5_RE.search(packed_info)
            if match is not None:
                resource_md5s.append(match.group(0).decode())
        return resource_md5s
