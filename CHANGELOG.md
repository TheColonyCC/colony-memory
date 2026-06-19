# Changelog

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
