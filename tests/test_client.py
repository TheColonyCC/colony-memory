from __future__ import annotations

import pytest

from colony_memory import ColonyMemory, QuotaExceeded, SnapshotNotFound


def test_backup_restore_roundtrip(vault):
    mem = ColonyMemory(backend=vault)
    docs = {"MEMORY.md": "facts", "soul.txt": "me"}
    info = mem.backup(docs)
    assert info.part_count >= 1
    assert mem.restore() == docs


def test_latest_points_to_newest(vault):
    mem = ColonyMemory(backend=vault)
    mem.backup({"v": "1"})
    info2 = mem.backup({"v": "2"})
    assert mem.latest().snapshot_id == info2.snapshot_id
    assert mem.restore() == {"v": "2"}


def test_list_and_prune(vault):
    mem = ColonyMemory(backend=vault)
    ids = [mem.backup({"n": str(i)}, label="x").snapshot_id for i in range(4)]
    assert len(mem.list_snapshots(label="x")) == 4
    deleted = mem.prune(label="x", keep=2)
    assert deleted == 2
    remaining = {s.snapshot_id for s in mem.list_snapshots(label="x")}
    assert ids[-1] in remaining  # newest kept
    assert mem.restore(label="x") == {"n": "3"}  # latest still restorable


def test_prune_never_deletes_live(vault):
    mem = ColonyMemory(backend=vault)
    mem.backup({"n": "0"}, label="y")
    # keep=0 would delete everything, but the live (latest) one is protected
    mem.prune(label="y", keep=0)
    assert mem.latest(label="y") is not None
    assert mem.restore(label="y") == {"n": "0"}


def test_restore_not_found(vault):
    mem = ColonyMemory(backend=vault)
    with pytest.raises(SnapshotNotFound):
        mem.restore()


def test_quota_exceeded(vault):
    vault.quota = 500  # tiny
    mem = ColonyMemory(backend=vault)
    with pytest.raises(QuotaExceeded):
        mem.backup({"big": "x" * 100000})


def test_status_passthrough(vault):
    mem = ColonyMemory(backend=vault)
    s = mem.status()
    assert set(s) >= {"quota_bytes", "used_bytes", "available_bytes", "file_count"}


def test_signed_via_client(vault):
    from colony_memory import Ed25519Signer

    mem = ColonyMemory(backend=vault, signer=Ed25519Signer.generate())
    info = mem.backup({"a": "b"})
    assert info.signed and info.issuer.startswith("did:key:z")
    assert mem.restore(verify=True) == {"a": "b"}


def test_progenly_bridge(vault):
    mem = ColonyMemory(backend=vault)
    export = mem.to_progenly_export({"MEMORY.md": "m"})
    assert export["memory"] == {"MEMORY.md": "m"}
    assert "memory_format" in export


def test_result_envelope_unwrap(vault):
    # Some callers/mocks wrap responses as {"result": {...}}; client should cope.
    class Wrapped:
        def __init__(self, v): self._v = v
        def vault_status(self): return {"result": self._v.vault_status()}
        def vault_list_files(self): return {"result": self._v.vault_list_files()}
        def vault_get_file(self, f): return {"result": self._v.vault_get_file(f)}
        def vault_upload_file(self, f, c): return {"result": self._v.vault_upload_file(f, c)}
        def vault_delete_file(self, f): return {"result": self._v.vault_delete_file(f)}

    mem = ColonyMemory(backend=Wrapped(vault))
    mem.backup({"a": "b"})
    assert mem.restore() == {"a": "b"}
