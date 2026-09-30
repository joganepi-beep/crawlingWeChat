from __future__ import annotations

from pathlib import Path

from wx_context import MediaDecoder as PublicMediaDecoder
from wx_context.media import MediaDecoder, MediaResolver


def test_media_resolver_prefers_high_quality_original_then_full_then_thumbnail(
    tmp_path: Path,
) -> None:
    (tmp_path / "image_t.dat").write_bytes(b"thumbnail")
    (tmp_path / "image.dat").write_bytes(b"full")
    (tmp_path / "image_h.dat").write_bytes(b"high")

    resolver = MediaResolver(tmp_path)

    assert resolver.preferred_path(tmp_path / "image_t.dat") == tmp_path / "image_h.dat"


def test_media_resolver_falls_back_to_thumbnail_when_no_original_exists(
    tmp_path: Path,
) -> None:
    thumbnail = tmp_path / "image_t.dat"
    thumbnail.write_bytes(b"thumbnail")

    assert MediaResolver(tmp_path).preferred_path("image.dat") == thumbnail


def test_media_decoder_removes_v2_aes_padding_before_xor_payload(tmp_path: Path) -> None:
    """V2 DAT streams keep a full AES block even when AES size is aligned."""
    aes_key = b"0123456789abcdef"
    xor_key = 0x69
    aes_plaintext = b"\xff\xd8" + (b"a" * 1022)
    pad = 16 - (len(aes_plaintext) % 16)
    padded_aes = aes_plaintext + bytes([pad]) * pad
    encrypted_aes = b"x" * len(padded_aes)
    xor_plaintext = b"-image-body-\xff\xd9"
    payload = encrypted_aes + bytes(byte ^ xor_key for byte in xor_plaintext)
    source = tmp_path / "image.dat"
    source.write_bytes(
        b"\x07\x08V2\x08\x07"
        + len(aes_plaintext).to_bytes(4, "little")
        + len(xor_plaintext).to_bytes(4, "little")
        + b"\x01"
        + payload
    )

    decoder = MediaDecoder(
        aes_key=aes_key,
        xor_key=xor_key,
        aes_decryptor=lambda ciphertext, key: (
            padded_aes if ciphertext == encrypted_aes and key == aes_key else b""
        ),
    )

    assert decoder.decrypt_v2(source) == (
        aes_plaintext + xor_plaintext
    )


def test_media_decoder_is_available_from_public_package() -> None:
    assert PublicMediaDecoder is MediaDecoder
