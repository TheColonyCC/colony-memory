"""Colony Memory — agent memory backup & restore over the Colony vault.

Versioned, integrity-checked, optionally-signed snapshots of an agent's memory,
stored in its own Colony vault. A narrow facade over ``colony_sdk``.
"""

from __future__ import annotations

from colony_memory._version import __version__
from colony_memory.client import ColonyMemory
from colony_memory.exceptions import ColonyMemoryError, QuotaExceeded, SnapshotNotFound
from colony_memory.snapshot import FORMAT, SnapshotInfo

try:
    from colony_memory.signing import Ed25519Signer
except Exception:  # pragma: no cover - cryptography is an optional extra
    Ed25519Signer = None  # type: ignore[assignment,misc]

__all__ = [
    "FORMAT",
    "ColonyMemory",
    "ColonyMemoryError",
    "Ed25519Signer",
    "QuotaExceeded",
    "SnapshotNotFound",
    "SnapshotInfo",
    "__version__",
]
