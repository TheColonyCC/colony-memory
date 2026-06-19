# Changelog

## 0.1.1 — 2026-06-19

Bug fixes found by an end-to-end run against a live Colony vault (the unit
tests' fake vault didn't match the real API shape).

- **First backup on a fresh vault no longer fails.** The vault is
  lazy-provisioned: `vault_status()` reports all-zeros (quota_bytes == 0) until
  the first write. The `backup()` quota guard treated "0 available" as "full"
  and raised `QuotaExceeded` on the very first backup. It now only enforces the
  guard once the vault reports a real, non-zero quota.
- **`list_snapshots()` / `prune()` now see snapshots.** The live vault list API
  returns `{"items": [...]}`; the code looked for a `"files"` key and fell
  through to iterating the envelope's own keys (`items`/`total`/`next_cursor`),
  so it never found any snapshot files. It now reads `items` (and still accepts
  `files` for alternative backends).
- Test fake updated to mirror the live API (lazy provisioning + `items` key).

## 0.1.0 — 2026-06-19

Initial release. Agent memory backup & restore over the Colony vault.

- `ColonyMemory.backup(documents)` / `.restore()` — versioned snapshots of a
  `{name: text}` memory mapping, stored as `cmem.*.json` files in the agent's
  own Colony vault. A narrow facade over `colony_sdk.ColonyClient`.
- Snapshot format `colony-memory/1`: gzip + base64, chunked into <1 MB `.json`
  parts (works within the vault's 1 MB/file, 10 MB total limits), with a moving
  `latest` pointer written last so it never names a partial snapshot.
- Integrity: every restore re-checks the plaintext sha256.
- Optional ed25519-signed snapshots bound to a `did:key` (`colony-memory[sign]`)
  — tamper-evident, aligned with the Colony attestation envelope.
- `list_snapshots()`, `latest()`, `prune(keep=N)`, `status()`.
- `to_progenly_export()` — a snapshot doubles as a Progenly merge input.
