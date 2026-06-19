"""Shared test fixtures: an in-memory fake of the Colony vault."""

from __future__ import annotations

import pytest


class FakeVault:
    """Minimal in-memory stand-in for the colony-sdk vault surface."""

    def __init__(self, quota: int = 10 * 1024 * 1024) -> None:
        self.files: dict[str, str] = {}
        self.quota = quota
        self.writes = 0

    def vault_status(self) -> dict:
        # Mirror the live API: the vault is lazy-provisioned, so before the first
        # write it reports all-zeros (quota only materialises on first upload).
        if not self.files:
            return {"quota_bytes": 0, "used_bytes": 0, "available_bytes": 0, "file_count": 0}
        used = sum(len(c.encode("utf-8")) for c in self.files.values())
        return {"quota_bytes": self.quota, "used_bytes": used,
                "available_bytes": self.quota - used, "file_count": len(self.files)}

    def vault_list_files(self) -> dict:
        # Live API envelope: {"items": [{"filename": ...}], "total", "next_cursor"}.
        return {"items": [{"filename": f} for f in self.files],
                "total": len(self.files), "next_cursor": None}

    def vault_get_file(self, filename: str) -> dict:
        if filename not in self.files:
            raise RuntimeError("not found")
        return {"filename": filename, "content": self.files[filename]}

    def vault_upload_file(self, filename: str, content: str) -> dict:
        self.writes += 1
        self.files[filename] = content
        return {"filename": filename}

    def vault_delete_file(self, filename: str) -> dict:
        self.files.pop(filename, None)
        return {}


@pytest.fixture
def vault() -> FakeVault:
    return FakeVault()
