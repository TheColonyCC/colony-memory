from __future__ import annotations

import json

import pytest

from colony_memory import snapshot as snap


def test_build_parse_roundtrip():
    docs = {"MEMORY.md": "# hi\nfacts", "soul.txt": "I am X", "s.json": '{"k":1}'}
    built = snap.build(docs, label="default", snapshot_id="sid1", created_at="t")
    parts = {f: c for f, c in built.files.items() if f != built.manifest_file}
    manifest = json.loads(built.files[built.manifest_file])
    assert snap.parse(manifest, parts) == docs


def test_chunking_multipart(monkeypatch):
    monkeypatch.setattr(snap, "PART_CHARS", 64)  # tiny → force many parts
    docs = {"big": "x" * 5000}
    built = snap.build(docs, label="l", snapshot_id="sid2", created_at="t")
    manifest = json.loads(built.files[built.manifest_file])
    assert manifest["part_count"] > 1
    parts = {f: c for f, c in built.files.items() if f != built.manifest_file}
    assert snap.parse(manifest, parts) == docs


def test_integrity_mismatch_raises():
    built = snap.build({"a": "b"}, label="l", snapshot_id="s", created_at="t")
    manifest = json.loads(built.files[built.manifest_file])
    manifest["plaintext_sha256"] = "sha256:" + "0" * 64
    parts = {f: c for f, c in built.files.items() if f != built.manifest_file}
    with pytest.raises(ValueError, match="integrity"):
        snap.parse(manifest, parts)


def test_missing_part_raises():
    built = snap.build({"a": "b"}, label="l", snapshot_id="s", created_at="t")
    manifest = json.loads(built.files[built.manifest_file])
    with pytest.raises(ValueError, match="missing part"):
        snap.parse(manifest, {})


def test_unsupported_format_raises():
    with pytest.raises(ValueError, match="unsupported"):
        snap.parse({"format": "nope"}, {})


def test_rejects_empty_and_nonstring():
    with pytest.raises(ValueError):
        snap.build({}, label="l", snapshot_id="s", created_at="t")
    with pytest.raises(ValueError):
        snap.build({"a": 1}, label="l", snapshot_id="s", created_at="t")  # type: ignore[dict-item]


def test_sanitize_label_and_filenames():
    assert snap.sanitize_label("My Label!") == "my-label"
    assert snap.sanitize_label("") == "default"
    assert snap.manifest_filename("L", "sid").startswith("cmem.l.sid")
    assert snap.part_filename("L", "sid", 2).endswith(".p2.json")
    assert snap.is_cortex_file("cmem.x.json")
    assert not snap.is_cortex_file("other.json")


def test_signed_build_and_verify():
    from colony_memory import Ed25519Signer

    signer = Ed25519Signer.generate()
    built = snap.build({"a": "b"}, label="l", snapshot_id="s", created_at="t", signer=signer)
    manifest = json.loads(built.files[built.manifest_file])
    assert manifest["signature"]["alg"] == "ed25519"
    assert manifest["signature"]["key_id"] == signer.did_key
    parts = {f: c for f, c in built.files.items() if f != built.manifest_file}
    assert snap.parse(manifest, parts, verify_signature=True) == {"a": "b"}


def test_tampered_signature_rejected():
    from colony_memory import Ed25519Signer

    signer = Ed25519Signer.generate()
    built = snap.build({"a": "b"}, label="l", snapshot_id="s", created_at="t", signer=signer)
    manifest = json.loads(built.files[built.manifest_file])
    manifest["created_at"] = "tampered"  # outside the signed bytes? no — created_at is signed
    parts = {f: c for f, c in built.files.items() if f != built.manifest_file}
    with pytest.raises(ValueError, match="signature does not verify"):
        snap.parse(manifest, parts, verify_signature=True)
