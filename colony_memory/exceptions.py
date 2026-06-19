"""Colony Memory exceptions."""

from __future__ import annotations


class ColonyMemoryError(RuntimeError):
    """Base class for Colony Memory errors."""


class SnapshotNotFound(ColonyMemoryError):
    """No snapshot exists for the requested label / snapshot_id."""


class QuotaExceeded(ColonyMemoryError):
    """The snapshot would exceed the agent's vault quota (10 MB free tier)."""
