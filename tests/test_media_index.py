from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from wx_context.media_index import WechatMediaLocator


def test_media_locator_uses_message_resource_md5_not_adapter_sample_path(
    tmp_path: Path,
) -> None:
    """Each image message resolves to its own media resource in a group cache."""
    session_id = "20192304905@chatroom"
    first_md5 = "79f682dc22c2551c1e32bd25d163456c"
    second_md5 = "c1f5c567ac4f66aadcded5c2aa6e12a3"
    resource_db = tmp_path / "message_resource.db"
    with sqlite3.connect(resource_db) as connection:
        connection.execute("CREATE TABLE ChatName2Id (user_name TEXT, update_time INTEGER)")
        connection.execute(
            """CREATE TABLE MessageResourceInfo (
                chat_id INTEGER,
                message_local_type INTEGER,
                message_create_time INTEGER,
                message_local_id INTEGER,
                packed_info BLOB
            )"""
        )
        connection.execute(
            "INSERT INTO ChatName2Id (user_name, update_time) VALUES (?, 0)",
            (session_id,),
        )
        connection.execute(
            """INSERT INTO MessageResourceInfo
               (chat_id, message_local_type, message_create_time, message_local_id, packed_info)
                VALUES (1, 3, 1790601466, 88, ?)""",
                (f"resource={first_md5}".encode(),),
        )
        connection.execute(
            """INSERT INTO MessageResourceInfo
               (chat_id, message_local_type, message_create_time, message_local_id, packed_info)
               VALUES (1, 3, 1790601472, 89, ?)""",
            (f"resource={second_md5}".encode(),),
        )

    attachment_root = tmp_path / "attach"
    image_dir = (
        attachment_root
        / hashlib.md5(session_id.encode()).hexdigest()
        / "2026-09"
        / "Img"
    )
    image_dir.mkdir(parents=True)
    expected = image_dir / f"{first_md5}.dat"
    expected.write_bytes(b"first full image")
    second_expected = image_dir / f"{second_md5}.dat"
    second_expected.write_bytes(b"second full image")
    (image_dir / "adapter_sample_h.dat").write_bytes(b"wrong image")

    locator = WechatMediaLocator(resource_db, attachment_root)

    # The CLI exposes minute precision while the source table stores seconds.
    assert locator.resolve(session_id, "2026-09-28T21:17:00") == expected
    assert locator.resolve(session_id, "2026-09-28T21:17:00") == second_expected


def test_media_locator_caches_resource_lookup_for_messages_in_same_minute(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = "room@chatroom"
    resource_db = tmp_path / "message_resource.db"
    attachment_root = tmp_path / "attach"
    image_dir = attachment_root / hashlib.md5(session_id.encode()).hexdigest() / "2026-09" / "Img"
    image_dir.mkdir(parents=True)
    first_md5 = "11111111111111111111111111111111"
    second_md5 = "22222222222222222222222222222222"
    (image_dir / f"{first_md5}.dat").write_bytes(b"first")
    (image_dir / f"{second_md5}.dat").write_bytes(b"second")
    locator = WechatMediaLocator(resource_db, attachment_root)
    calls = 0

    def lookup(_session_id: str, _minute_start: int) -> list[str]:
        nonlocal calls
        calls += 1
        return [first_md5, second_md5]

    monkeypatch.setattr(locator, "_resource_md5s", lookup)

    assert locator.resolve(session_id, "2026-09-28T21:17:00") == image_dir / f"{first_md5}.dat"
    assert locator.resolve(session_id, "2026-09-28T21:17:00") == image_dir / f"{second_md5}.dat"
    assert calls == 1
