"""Domain-specific exceptions for wx-context."""


class ValidationError(ValueError):
    """Raised when caller-provided data violates a safety constraint."""


class DependencyError(RuntimeError):
    """Raised when the separately installed read-only dependency fails."""


class StorageError(RuntimeError):
    """Raised when local wx-context storage cannot be read or written."""
