"""Colony Memory snapshot format (``colony-memory/1``).

A *snapshot* is a content-agnostic, integrity-checked, optionally-signed backup
of an agent's memory — an arbitrary ``{name: text}`` mapping — laid out across
the flat Colony vault as a manifest + N chunk parts + a moving "latest" pointer.

Why this shape, given the vault's limits (1 MB per file, 10 MB total, flat
namespace, ``.json`` among the allowed extensions):

- **gzip + base64**: the documents are serialised to canonical JSON, gzipped
  (memory text compresses heavily, stretching the 10 MB quota), then base64'd so
  the payload is ASCII and safe inside a JSON file with no escape-inflation.
- **chunking**: the base64 blob is split into <1 MB ``.json`` parts so a snapshot
  larger than the per-file cap still fits.
- **integrity**: the manifest records the plaintext sha256; restore re-checks it,
  so a corrupted or truncated restore fails loudly instead of silently.
- **signature (optional)**: an ed25519 signature over the canonicalised manifest
  binds the snapshot to a ``did:key`` — tamper-evident, and aligned with the
  Colony attestation-envelope ethos. Requires ``colony-memory[sign]``.

This module is pure (no network): it turns documents into vault files and back.
:mod:`colony_memory.client` does the actual vault I/O.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from dataclasses import dataclass, field

FORMAT = "colony-memory/1"
LATEST_FORMAT = "colony-memory/latest/1"

#: Max base64 characters per part file. The part is a small JSON wrapper around
#: this slice; base64 is ASCII so the encoded file stays comfortably under the
#: vault's 1 MB/file cap.
PART_CHARS = 700_000

_LABEL_RE = re.compile(r"[^a-z0-9_-]+")


def _canonical(value: object) -> bytes:
    """RFC 8785-ish canonical JSON: key-sorted, compact, UTF-8 (float-free)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sanitize_label(label: str) -> str:
    """Normalise a label to ``[a-z0-9_-]`` so it's safe in a flat filename."""
    cleaned = _LABEL_RE.sub("-", (label or "default").strip().lower()).strip("-")
    return cleaned or "default"


def manifest_filename(label: str, snapshot_id: str) -> str:
    return f"cmem.{sanitize_label(label)}.{snapshot_id}.manifest.json"


def part_filename(label: str, snapshot_id: str, seq: int) -> str:
    return f"cmem.{sanitize_label(label)}.{snapshot_id}.p{seq}.json"


def latest_filename(label: str) -> str:
    return f"cmem.{sanitize_label(label)}.latest.json"


def is_cortex_file(filename: str) -> bool:
    return filename.startswith("cmem.") and filename.endswith(".json")


@dataclass
class SnapshotInfo:
    """Lightweight handle to a stored snapshot (manifest metadata)."""

    snapshot_id: str
    label: str
    created_at: str
    doc_names: list[str]
    part_count: int
    byte_size: int
    plaintext_sha256: str
    signed: bool = False
    issuer: str | None = None


@dataclass
class BuiltSnapshot:
    """A snapshot serialised to vault files, ready to write."""

    info: SnapshotInfo
    manifest_file: str
    files: dict[str, str] = field(default_factory=dict)  # filename -> JSON content


def build(
    documents: dict[str, str],
    *,
    label: str,
    snapshot_id: str,
    created_at: str,
    signer: object | None = None,
) -> BuiltSnapshot:
    """Serialise ``documents`` into vault files.

    ``signer`` (optional) is anything with ``sign(message: bytes) -> bytes`` and
    a ``did_key`` / ``key_id`` attribute (see :class:`colony_memory.Ed25519Signer`);
    when given, the manifest is ed25519-signed over its canonical form.
    """
    if not isinstance(documents, dict) or not documents:
        raise ValueError("documents must be a non-empty {name: text} mapping")
    for k, v in documents.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("documents keys and values must both be strings")

    plaintext = _canonical(documents)
    plaintext_sha256 = "sha256:" + hashlib.sha256(plaintext).hexdigest()
    blob = base64.b64encode(gzip.compress(plaintext, mtime=0)).decode("ascii")

    parts = [blob[i : i + PART_CHARS] for i in range(0, len(blob), PART_CHARS)] or [""]
    files: dict[str, str] = {}
    part_files: list[str] = []
    for seq, chunk in enumerate(parts):
        fn = part_filename(label, snapshot_id, seq)
        files[fn] = json.dumps({"format": FORMAT, "snapshot_id": snapshot_id, "seq": seq, "b64": chunk})
        part_files.append(fn)

    manifest: dict[str, object] = {
        "format": FORMAT,
        "snapshot_id": snapshot_id,
        "label": sanitize_label(label),
        "created_at": created_at,
        "codec": "gzip+base64",
        "doc_names": sorted(documents),
        "plaintext_sha256": plaintext_sha256,
        "part_count": len(part_files),
        "part_files": part_files,
        "byte_size": len(plaintext),
        "blob_chars": len(blob),
    }
    if signer is not None:
        sig = base64.urlsafe_b64encode(signer.sign(_canonical(manifest))).rstrip(b"=").decode("ascii")  # type: ignore[attr-defined]
        manifest["signature"] = {
            "alg": "ed25519",
            "key_id": getattr(signer, "did_key", None) or getattr(signer, "key_id", None),
            "sig": sig,
        }

    mfile = manifest_filename(label, snapshot_id)
    files[mfile] = json.dumps(manifest)
    info = SnapshotInfo(
        snapshot_id=snapshot_id,
        label=sanitize_label(label),
        created_at=created_at,
        doc_names=sorted(documents),
        part_count=len(part_files),
        byte_size=len(plaintext),
        plaintext_sha256=plaintext_sha256,
        signed="signature" in manifest,
        issuer=(manifest.get("signature") or {}).get("key_id") if "signature" in manifest else None,  # type: ignore[union-attr]
    )
    return BuiltSnapshot(info=info, manifest_file=mfile, files=files)


def info_from_manifest(manifest: dict) -> SnapshotInfo:
    sig = manifest.get("signature") or {}
    return SnapshotInfo(
        snapshot_id=str(manifest.get("snapshot_id", "")),
        label=str(manifest.get("label", "default")),
        created_at=str(manifest.get("created_at", "")),
        doc_names=list(manifest.get("doc_names", [])),
        part_count=int(manifest.get("part_count", 0)),
        byte_size=int(manifest.get("byte_size", 0)),
        plaintext_sha256=str(manifest.get("plaintext_sha256", "")),
        signed=bool(sig),
        issuer=sig.get("key_id") if sig else None,
    )


def parse(manifest: dict, parts: dict[str, str], *, verify_signature: bool = False) -> dict[str, str]:
    """Reassemble documents from a manifest + its part files.

    ``parts`` maps part filename -> the part file's JSON content. Always checks
    the plaintext sha256; if ``verify_signature`` is set and the manifest is
    signed, also verifies the ed25519 signature against its ``did:key`` (raises
    on mismatch). Returns the ``{name: text}`` documents.
    """
    if manifest.get("format") != FORMAT:
        raise ValueError(f"unsupported snapshot format: {manifest.get('format')!r}")
    if verify_signature and manifest.get("signature"):
        _verify_signature(manifest)

    blob = ""
    for fn in manifest.get("part_files", []):
        raw = parts.get(fn)
        if raw is None:
            raise ValueError(f"missing part file: {fn}")
        blob += json.loads(raw).get("b64", "")

    plaintext = gzip.decompress(base64.b64decode(blob)) if blob else b"{}"
    got = "sha256:" + hashlib.sha256(plaintext).hexdigest()
    if got != manifest.get("plaintext_sha256"):
        raise ValueError("snapshot integrity check failed (plaintext sha256 mismatch)")
    documents = json.loads(plaintext)
    if not isinstance(documents, dict):
        raise ValueError("decoded snapshot is not a documents object")
    return documents


def _verify_signature(manifest: dict) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:  # pragma: no cover - optional dep
        raise RuntimeError("signature verification needs `colony-memory[sign]` (cryptography)") from e

    sig = manifest["signature"]
    if sig.get("alg") != "ed25519":
        raise ValueError(f"unsupported signature alg: {sig.get('alg')!r}")
    pub = _ed25519_pub_from_did_key(str(sig.get("key_id", "")))
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    raw = base64.urlsafe_b64decode(sig["sig"] + "=" * ((4 - len(sig["sig"]) % 4) % 4))
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(raw, _canonical(unsigned))
    except InvalidSignature as e:
        raise ValueError("snapshot signature does not verify") from e


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _ed25519_pub_from_did_key(did: str) -> bytes:
    if not did.startswith("did:key:z"):
        raise ValueError("not a base58btc did:key")
    n = 0
    for ch in did[len("did:key:z") :]:
        i = _B58.find(ch)
        if i < 0:
            raise ValueError(f"invalid base58 char: {ch!r}")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if body[:2] != b"\xed\x01":
        raise ValueError("did:key multicodec is not ed25519")
    pub = body[2:]
    if len(pub) != 32:
        raise ValueError("ed25519 public key must be 32 bytes")
    return pub
