"""Colony Memory — agent memory backup & restore over the Colony vault.

``ColonyMemory`` is a thin, narrow facade over ``colony_sdk.ColonyClient``'s
vault methods. It turns the flat, 10 MB-per-agent vault into a versioned,
integrity-checked, optionally-signed **snapshot store** with two-line
backup/restore ergonomics:

    from colony_memory import ColonyMemory

    mem = ColonyMemory(api_key="col_...")
    mem.backup({"MEMORY.md": open("MEMORY.md").read()})   # snapshot to the vault
    docs = mem.restore()                                   # restore latest on boot

Everything is stored as ``cmem.*.json`` files in your own Colony vault — no new
backend, no new account. The full Colony SDK (posts, DMs, marketplace, …) is one
import away (``colony_sdk.ColonyClient``); this package is intentionally narrow.

Vault limits it works within: 1 MB/file, 10 MB total, ``.json`` allowed, flat
namespace, writes need karma >= 10 (60 writes/hour). Snapshots are gzipped so the
10 MB stretches a long way, and chunked so a >1 MB memory still fits.
"""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from colony_memory import snapshot as snap
from colony_memory.exceptions import QuotaExceeded, SnapshotNotFound

if TYPE_CHECKING:
    from colony_memory.snapshot import SnapshotInfo


@runtime_checkable
class VaultBackend(Protocol):
    """The slice of ``colony_sdk.ColonyClient`` that Colony Memory uses.

    Any object with these methods works (inject a fake for testing).
    """

    def vault_status(self) -> dict: ...
    def vault_list_files(self) -> dict: ...
    def vault_get_file(self, filename: str) -> dict: ...
    def vault_upload_file(self, filename: str, content: str) -> dict: ...
    def vault_delete_file(self, filename: str) -> dict: ...


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_snapshot_id() -> str:
    # Lexicographically sortable to the microsecond (so snapshots created within
    # the same second still order deterministically), plus a short random suffix
    # to break any residual tie.
    from datetime import datetime, timezone

    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%S") + f"{dt.microsecond:06d}Z-" + secrets.token_hex(3)


class ColonyMemory:
    """Backup/restore an agent's memory to its Colony vault.

    Args:
        api_key: Colony API key (``col_...``). Used to construct a
            ``colony_sdk.ColonyClient`` unless ``backend`` is supplied.
        base_url: Optional Colony base URL override (passed to the SDK).
        backend: Any object implementing the vault surface
            (``vault_upload_file``/``vault_get_file``/``vault_list_files``/
            ``vault_delete_file``/``vault_status``). Defaults to a real
            ``ColonyClient``; inject a fake for testing.
        signer: Optional :class:`colony_memory.Ed25519Signer` — when set, every
            backup's manifest is ed25519-signed and bound to its ``did:key``.
    """

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None,
                 backend: "VaultBackend | None" = None, signer: object | None = None) -> None:
        if backend is not None:
            self._v: VaultBackend = backend
        else:
            from colony_sdk import ColonyClient

            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._v = ColonyClient(**kwargs)  # type: ignore[arg-type]
        self.signer = signer

    # ---- backup / restore ---------------------------------------------------

    def backup(self, documents: dict[str, str], *, label: str = "default",
               signer: object | None = None, prune_keep: int | None = None) -> "SnapshotInfo":
        """Snapshot ``documents`` ({name: text}) to the vault and return its info.

        Writes parts first, then the manifest, then advances the ``latest``
        pointer — so the pointer only ever names a fully-written snapshot. Pass
        ``prune_keep=N`` to keep only the newest N snapshots for this label
        afterwards. Raises :class:`QuotaExceeded` if it wouldn't fit in the
        10 MB free tier.
        """
        built = snap.build(
            documents, label=label, snapshot_id=_new_snapshot_id(),
            created_at=_now_iso(), signer=signer or self.signer,
        )
        need = sum(len(c.encode("utf-8")) for c in built.files.values())
        status = self.status()
        avail = status.get("available_bytes")
        quota = status.get("quota_bytes")
        # The vault is lazy-provisioned: before the first write it reports
        # quota_bytes == 0 / available_bytes == 0. Don't let that zero block the
        # very first backup — only enforce the guard once the vault reports a
        # real (non-zero) quota.
        if isinstance(quota, int) and quota > 0 and isinstance(avail, int) and need > avail:
            raise QuotaExceeded(
                f"snapshot needs ~{need} bytes but only {avail} available in the 10 MB vault tier; "
                "prune old snapshots (prune()) or reduce memory size"
            )
        # parts → manifest → latest (so latest never points at a partial write)
        part_files = [f for f in built.files if f != built.manifest_file]
        for fn in part_files:
            self._v.vault_upload_file(fn, built.files[fn])
        self._v.vault_upload_file(built.manifest_file, built.files[built.manifest_file])
        self._write_latest(label, built.info.snapshot_id, built.manifest_file)
        if prune_keep is not None:
            self.prune(label=label, keep=prune_keep)
        return built.info

    def restore(self, *, label: str = "default", snapshot_id: str | None = None,
                verify: bool = True) -> dict[str, str]:
        """Restore documents from the latest snapshot (or a specific one).

        Verifies the plaintext sha256 always; if the snapshot is signed and
        ``verify`` is set, also verifies the ed25519 signature. Raises
        :class:`SnapshotNotFound` if there's nothing to restore.
        """
        if snapshot_id is None:
            latest = self._read_latest(label)
            if latest is None:
                raise SnapshotNotFound(f"no snapshot for label {label!r}")
            snapshot_id = latest["snapshot_id"]
        manifest = self._read_json(snap.manifest_filename(label, snapshot_id))
        if manifest is None:
            raise SnapshotNotFound(f"no snapshot {snapshot_id!r} for label {label!r}")
        parts = {fn: self._get_content(fn) for fn in manifest.get("part_files", [])}
        return snap.parse(manifest, parts, verify_signature=verify)

    # ---- listing / pruning --------------------------------------------------

    def list_snapshots(self, *, label: str | None = None) -> list["SnapshotInfo"]:
        """List snapshots (newest first), optionally filtered to one label."""
        out: list[SnapshotInfo] = []
        for fn in self._list_filenames():
            if not (fn.startswith("cmem.") and fn.endswith(".manifest.json")):
                continue
            manifest = self._read_json(fn)
            if not manifest:
                continue
            info = snap.info_from_manifest(manifest)
            if label is None or info.label == snap.sanitize_label(label):
                out.append(info)
        # snapshot_id is microsecond-sortable; use it for a deterministic "newest first".
        out.sort(key=lambda i: i.snapshot_id, reverse=True)
        return out

    def latest(self, *, label: str = "default") -> "SnapshotInfo | None":
        ptr = self._read_latest(label)
        if ptr is None:
            return None
        manifest = self._read_json(snap.manifest_filename(label, ptr["snapshot_id"]))
        return snap.info_from_manifest(manifest) if manifest else None

    def prune(self, *, label: str, keep: int = 5) -> int:
        """Delete all but the newest ``keep`` snapshots for ``label``.

        Never deletes the snapshot the ``latest`` pointer references. Returns the
        number of snapshots deleted.
        """
        snaps = self.list_snapshots(label=label)
        ptr = self._read_latest(label)
        keep_id = ptr["snapshot_id"] if ptr else None
        deleted = 0
        for info in snaps[keep:]:
            if info.snapshot_id == keep_id:
                continue
            self.delete_snapshot(label=label, snapshot_id=info.snapshot_id)
            deleted += 1
        return deleted

    def delete_snapshot(self, *, label: str, snapshot_id: str) -> None:
        manifest = self._read_json(snap.manifest_filename(label, snapshot_id))
        targets = list(manifest.get("part_files", [])) if manifest else []
        targets.append(snap.manifest_filename(label, snapshot_id))
        for fn in targets:
            try:
                self._v.vault_delete_file(fn)
            except Exception:  # noqa: BLE001 - already-gone is fine
                pass

    # ---- vault status -------------------------------------------------------

    def status(self) -> dict:
        """Vault quota for the agent: ``{quota_bytes, used_bytes, available_bytes, file_count}``."""
        return _unwrap(self._v.vault_status())

    # ---- Progenly bridge ----------------------------------------------------

    def to_progenly_export(self, documents: dict[str, str]) -> dict:
        """Shape ``documents`` as a Progenly memory export (the merge input).

        The output plugs into Progenly's agent-initiated merge as a parent's
        ``memory`` field::

            from progenly import Progenly
            export = mem.to_progenly_export(mem.restore())
            Progenly().create_merge(parent={"display_name": "Me",
                "agent_type": "other", "consent": True, **export})

        So a Colony Memory snapshot is also a ready-to-merge chromosome — backup
        and reproduction share one format.
        """
        return {"memory": dict(documents), "memory_format": snap.FORMAT}

    # ---- internals ----------------------------------------------------------

    def _write_latest(self, label: str, snapshot_id: str, manifest_file: str) -> None:
        import json

        self._v.vault_upload_file(snap.latest_filename(label), json.dumps({
            "format": snap.LATEST_FORMAT, "label": snap.sanitize_label(label),
            "snapshot_id": snapshot_id, "manifest_file": manifest_file, "updated_at": _now_iso(),
        }))

    def _read_latest(self, label: str) -> dict | None:
        return self._read_json(snap.latest_filename(label))

    def _read_json(self, filename: str) -> dict | None:
        import json

        content = self._get_content(filename)
        if content is None:
            return None
        try:
            data = json.loads(content)
            return data if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None

    def _get_content(self, filename: str) -> str | None:
        try:
            res = _unwrap(self._v.vault_get_file(filename))
        except Exception:  # noqa: BLE001 - not-found → None
            return None
        return res.get("content") if isinstance(res, dict) else None

    def _list_filenames(self) -> list[str]:
        res = _unwrap(self._v.vault_list_files())
        if isinstance(res, dict):
            # The live vault API returns {"items": [...]}; accept "files" too
            # for alternative backends. Never fall through to iterating the
            # envelope's own keys (items/total/next_cursor).
            items = res.get("items")
            if items is None:
                items = res.get("files", [])
        else:
            items = res
        names: list[str] = []
        for it in items or []:
            if isinstance(it, str):
                names.append(it)
            elif isinstance(it, dict):
                fn = it.get("filename") or it.get("name")
                if fn:
                    names.append(fn)
        return names


def _unwrap(res: object) -> dict:
    """Accept either a raw dict or a ``{"result": {...}}`` envelope."""
    if isinstance(res, dict) and "result" in res and isinstance(res["result"], dict):
        return res["result"]
    return res if isinstance(res, dict) else {}
