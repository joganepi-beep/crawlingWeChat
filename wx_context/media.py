"""Local WeChat media path selection helpers."""

from __future__ import annotations

from pathlib import Path
import re
import struct
from collections.abc import Callable


_MEDIA_NAME_RE = re.compile(r"^(?P<stem>.+?)(?:_(?:t|h|o))?(?P<suffix>\.[^.]+)?$")
_V2_HEADER = b"\x07\x08V2\x08\x07"


class MediaDecoder:
    """Decode WeChat V2 DAT media without changing the source file.

    The decoder is portable across Windows and macOS.  Its only native
    dependency is the optional ``ffmpeg`` executable needed separately to
    render WXGF/HEVC payloads; direct JPEG/PNG payloads do not need it.
    """

    def __init__(
        self,
        *,
        aes_key: bytes,
        xor_key: int,
        aes_decryptor: Callable[[bytes, bytes], bytes] | None = None,
    ) -> None:
        if not isinstance(aes_key, bytes) or len(aes_key) != 16:
            raise ValueError("aes_key must be exactly 16 bytes")
        if isinstance(xor_key, bool) or not isinstance(xor_key, int) or not 0 <= xor_key <= 255:
            raise ValueError("xor_key must be an integer from 0 through 255")
        self._aes_key = aes_key
        self._xor_key = xor_key
        self._aes_decryptor = aes_decryptor or _decrypt_aes_ecb

    def decrypt_v2(self, source: str | Path) -> bytes:
        """Return plaintext from one V2 DAT file, removing AES PKCS#7 padding."""
        data = Path(source).read_bytes()
        if len(data) < 15 or not data.startswith(_V2_HEADER):
            raise ValueError("source is not a WeChat V2 DAT file")
        aes_size, xor_size = struct.unpack("<II", data[6:14])
        aligned_aes_size = aes_size + (16 - (aes_size % 16))
        payload = data[15:]
        if aligned_aes_size > len(payload) or xor_size > len(payload) - aligned_aes_size:
            raise ValueError("invalid WeChat V2 DAT payload sizes")

        padded_aes = self._aes_decryptor(payload[:aligned_aes_size], self._aes_key)
        aes_plaintext = _remove_pkcs7_padding(padded_aes)
        raw_size = len(payload) - aligned_aes_size - xor_size
        raw_data = payload[aligned_aes_size : aligned_aes_size + raw_size]
        xor_data = payload[aligned_aes_size + raw_size :]
        return aes_plaintext + raw_data + bytes(byte ^ self._xor_key for byte in xor_data)


def _decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    try:
        from Crypto.Cipher import AES
    except ImportError as error:
        raise RuntimeError("pycryptodome is required to decrypt WeChat V2 media") from error
    return AES.new(key, AES.MODE_ECB).decrypt(ciphertext)


def _remove_pkcs7_padding(data: bytes) -> bytes:
    if not data:
        raise ValueError("invalid empty AES payload")
    padding_size = data[-1]
    if not 1 <= padding_size <= 16 or data[-padding_size:] != bytes([padding_size]) * padding_size:
        raise ValueError("invalid AES PKCS#7 padding")
    return data[:-padding_size]


class MediaResolver:
    """Choose the best local media file, preferring original resources.

    WeChat commonly keeps image variants as ``<md5>_t.dat`` (thumbnail),
    ``<md5>.dat`` (full resource), and ``<md5>_h.dat`` (high-quality resource).
    The resolver never reads or copies the file; it only returns an existing
    path, so callers can decide whether and how to decode it.
    """

    _VARIANT_ORDER = ("_h", "", "_o", "_t")

    def __init__(self, media_dir: str | Path) -> None:
        self.media_dir = Path(media_dir)

    def candidates(self, source: str | Path) -> tuple[Path, ...]:
        """Return candidate paths in original-first order."""
        source_path = Path(source)
        directory = source_path.parent if source_path.parent != Path("") else self.media_dir
        if not source_path.is_absolute():
            directory = self.media_dir
        match = _MEDIA_NAME_RE.match(source_path.name)
        if match is None:
            return ()
        stem = match.group("stem")
        extension = match.group("suffix") or ".dat"
        return tuple(directory / f"{stem}{variant}{extension}" for variant in self._VARIANT_ORDER)

    def preferred_path(self, source: str | Path) -> Path | None:
        """Return the best existing variant or ``None`` when none is cached."""
        for candidate in self.candidates(source):
            if candidate.is_file():
                return candidate
        return None
