"""Optional ed25519 signing for snapshots (``colony-memory[sign]``).

A snapshot's manifest can be signed so a restore is tamper-evident and bound to
a ``did:key`` — the same primitive the Colony attestation envelope uses.
"""

from __future__ import annotations

import base64


class Ed25519Signer:
    """Signs snapshot manifests with an ed25519 seed; exposes its ``did:key``.

    >>> signer = Ed25519Signer.generate()        # or Ed25519Signer(seed_bytes)
    >>> mem.backup(docs, signer=signer)          # manifest is signed
    >>> signer.did_key                            # did:key:z6Mk...
    """

    _MULTICODEC = b"\xed\x01"
    _B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

    def __init__(self, seed: bytes) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        if len(seed) != 32:
            raise ValueError("ed25519 seed must be 32 bytes")
        self._seed = seed
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self.did_key = self._make_did_key()

    @classmethod
    def generate(cls) -> "Ed25519Signer":
        import os

        return cls(os.urandom(32))

    @property
    def seed(self) -> bytes:
        """The 32-byte seed — persist this to reuse the same did:key."""
        return self._seed

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)

    def _make_did_key(self) -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        pub = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        payload = self._MULTICODEC + pub
        n = int.from_bytes(payload, "big")
        out = ""
        while n > 0:
            n, r = divmod(n, 58)
            out = self._B58[r] + out
        out = "1" * (len(payload) - len(payload.lstrip(b"\x00"))) + out
        return "did:key:z" + out
