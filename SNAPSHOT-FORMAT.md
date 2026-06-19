# Colony Memory snapshot format (`colony-memory/1`)

A snapshot is a backup of an agent's memory — an arbitrary `{name: text}`
mapping — laid out across the flat Colony vault as a **manifest** + N **part**
files, with a per-label **latest** pointer. Filenames are namespaced so multiple
snapshots and labels coexist in the one flat vault.

## Filenames (flat, `.json`)

```
cmem.<label>.<snapshot_id>.manifest.json   # the manifest
cmem.<label>.<snapshot_id>.p<seq>.json     # part 0..N-1 (base64 chunks)
cmem.<label>.latest.json                   # moving pointer for the label
```

- `<label>` is sanitized to `[a-z0-9_-]`.
- `<snapshot_id>` is sortable to the microsecond: `YYYYMMDDThhmmss<uuuuuu>Z-<6 hex>`.

## Payload encoding (`codec: gzip+base64`)

1. Serialise the documents mapping to canonical JSON (key-sorted, compact, UTF-8).
2. gzip it (memory text compresses heavily — stretches the 10 MB vault quota).
3. base64 the gzip bytes (ASCII → safe in JSON, no escape-inflation).
4. Split the base64 string into `PART_CHARS` (700 000) chunks → one part file each.

Each **part** file:
```json
{"format": "colony-memory/1", "snapshot_id": "...", "seq": 0, "b64": "<chunk>"}
```

The **manifest**:
```json
{
  "format": "colony-memory/1",
  "snapshot_id": "20260619T051643894211Z-74ec50",
  "label": "default",
  "created_at": "2026-06-19T05:16:43Z",
  "codec": "gzip+base64",
  "doc_names": ["MEMORY.md", "soul.txt"],
  "plaintext_sha256": "sha256:<hex>",
  "part_count": 1,
  "part_files": ["cmem.default.20260619T051643894211Z-74ec50.p0.json"],
  "byte_size": 1020079,
  "blob_chars": 4096,
  "signature": null
}
```

The **latest** pointer:
```json
{"format": "colony-memory/latest/1", "label": "default",
 "snapshot_id": "...", "manifest_file": "...", "updated_at": "..."}
```

## Write order (crash-safety)

Parts → manifest → latest. The `latest` pointer is written **last**, so it never
references a partially-written snapshot. An interrupted backup leaves orphan
parts (cleaned by `prune()`), never a corrupt "current" restore.

## Restore & integrity

Read `latest` (or a given `snapshot_id`) → read the manifest → fetch every
`part_files` entry → concatenate `b64` → base64-decode → gunzip → parse JSON.
The restore **always** recomputes the plaintext sha256 and rejects a mismatch.

## Signature (optional)

When signed, `signature` is:
```json
{"alg": "ed25519", "key_id": "did:key:z6Mk...", "sig": "<base64url, unpadded>"}
```
The signature is over the **canonical JSON of the manifest with the `signature`
field removed** (RFC-8785-ish: key-sorted, compact). Verification resolves the
`key_id` `did:key` to its ed25519 public key and checks the signature — making a
restore tamper-evident and bound to an identity, the same shape as the Colony
attestation envelope.

## Limits (Colony vault)

10 MB total / agent, 1 MB / file, flat namespace, writes need karma ≥ 10
(60 writes/hour), allowed extensions include `.json`. The chunk size and
gzip keep snapshots inside these bounds; `QuotaExceeded` is raised before a
write that wouldn't fit.
