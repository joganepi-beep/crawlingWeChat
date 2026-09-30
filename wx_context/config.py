"""Versioned local configuration for the explicit group allowlist."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .errors import StorageError, ValidationError


CONFIG_FILENAME = "config.json"
CONFIG_SCHEMA_VERSION = 1


@dataclass
class ContextConfig:
    """Persisted local configuration, deny-by-default for group access."""

    data_dir: Path
    allowed_groups: dict[str, str]

    @property
    def path(self) -> Path:
        """Return the configuration file's location."""
        return self.data_dir / CONFIG_FILENAME

    def allow_group(self, session_id: str, display_name: str) -> None:
        """Add one trimmed group session to the allowlist and persist it."""
        session_id = session_id.strip()
        display_name = display_name.strip()
        if not session_id or not display_name:
            raise ValidationError("session id and group name must not be blank")

        try:
            with _configuration_lock(self.data_dir):
                allowed_groups = _load_allowed_groups(self.path)
                if session_id in allowed_groups:
                    raise ValidationError(
                        f"session id is already allowlisted: {session_id}"
                    )
                updated_groups = {**allowed_groups, session_id: display_name}
                _write_json_atomically(
                    self.path,
                    _config_payload(updated_groups),
                )
        except OSError as error:
            raise StorageError("could not save local configuration") from error

        self.allowed_groups = updated_groups

    def save(self) -> None:
        """Atomically persist the current configuration."""
        try:
            with _configuration_lock(self.data_dir):
                _write_json_atomically(self.path, _config_payload(self.allowed_groups))
        except OSError as error:
            raise StorageError("could not save local configuration") from error


def load_config(data_dir: Path | str) -> ContextConfig:
    """Load local configuration, creating an empty versioned file if missing."""
    directory = Path(data_dir)
    config = ContextConfig(data_dir=directory, allowed_groups={})

    try:
        with _configuration_lock(directory):
            if not config.path.exists():
                _write_json_atomically(config.path, _config_payload({}))
                return config
            allowed_groups = _load_allowed_groups(config.path)
    except OSError as error:
        raise StorageError("could not load local configuration") from error

    return ContextConfig(data_dir=directory, allowed_groups=dict(allowed_groups))


def _config_payload(allowed_groups: dict[str, str]) -> dict[str, object]:
    """Return the versioned JSON payload for an allowlist."""
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "allowed_groups": allowed_groups,
    }


def _load_allowed_groups(path: Path) -> dict[str, str]:
    """Load and validate a versioned allowlist payload from disk."""
    try:
        with path.open(encoding="utf-8") as config_file:
            payload = json.load(config_file)
        schema_version = payload["schema_version"]
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != CONFIG_SCHEMA_VERSION
        ):
            raise ValueError("unsupported configuration schema version")
        allowed_groups = payload["allowed_groups"]
        if not isinstance(allowed_groups, dict) or not all(
            isinstance(session_id, str) and isinstance(display_name, str)
            for session_id, display_name in allowed_groups.items()
        ):
            raise ValueError("allowed_groups must be an object of strings")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise StorageError("could not load local configuration") from error
    return dict(allowed_groups)


@contextmanager
def _configuration_lock(data_dir: Path) -> Iterator[None]:
    """Hold an exclusive lock while reading and replacing configuration."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / ".config.lock"
    with lock_path.open("a+b") as lock_file:
        lock_file.write(b"\0")
        lock_file.flush()
        lock_file.seek(0)
        if _is_windows():
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock_file.seek(0)
            if _is_windows():
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    """Write JSON to a sibling temporary file, then replace the target."""
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as config_file:
            json.dump(payload, config_file, ensure_ascii=False, indent=2, sort_keys=True)
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())
        _harden_written_file(temporary_path)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _harden_written_file(path: Path) -> None:
    """Apply owner-only permissions before atomically publishing a config file."""
    if _is_windows():
        identity = _windows_identity()
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{identity}:(F)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError("could not apply restrictive Windows permissions")
        return
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _windows_identity() -> str:
    """Return the account that should retain access to local config data."""
    result = subprocess.run(
        ["whoami"],
        capture_output=True,
        text=True,
        check=False,
    )
    identity = result.stdout.strip()
    if result.returncode != 0 or not identity:
        raise OSError("could not determine Windows account for local permissions")
    return identity


def _is_windows() -> bool:
    """Keep platform checks mockable without changing global os state in tests."""
    return os.name == "nt"
