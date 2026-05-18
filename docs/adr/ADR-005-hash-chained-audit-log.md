# ADR-005: Hash-Chained Audit Log

## Status

Accepted

## Context

PMACS maintains an immutable record of every state transition, trade decision, conviction change, and operator action. This audit log is central to the trust contract (Source.md §4.1): the operator must be able to verify that the system's recorded history has not been tampered with.

A simple append-only file with `fsync` after each write guarantees that entries are not lost to crashes and that the file cannot be silently truncated. However, append-only does not detect mid-file tampering: an attacker with write access to the log file could modify an existing entry in place without detection.

The audit log is written by `pmacs-nervous` (the only process with write access) and verified by `pmacs-cortex` (which runs periodic integrity checks).

## Decision

Use a hash-chained audit log where each entry includes the SHA256 of the previous entry (`prev_sha256`). The chain is anchored by a hardcoded genesis hash.

Every audit event includes: `event_id` (UUID), `event_type` (from canonical registry in Architecture.md §5.5), `cycle_id` (required, never None), `timestamp`, `payload`, and `prev_sha256`. The hash is computed over the canonical JSON representation of the entry (using `canonical_json()`, not `json.dumps()`).

The chain is verified by `ops/audit_chain_verify.py` and by cortex's periodic self-check. Tampering with any single entry breaks the chain from that point forward.

## Consequences

**Positive:**

- Detects both truncation (append-only property) and mid-file tampering (hash chain property). An attacker would need to rewrite every subsequent entry to maintain a valid chain.
- The cost is minimal: ~32 bytes per entry and one SHA256 computation per write. SHA256 on an M1 Max is hardware-accelerated.
- The hash chain provides a cryptographically strong integrity guarantee without requiring a PKI or external timestamping service.
- `audit_chain_verify.py` can be run offline to validate the entire history, supporting post-incident forensics.

**Negative:**

- The hash chain means entries are not independently verifiable; verification requires scanning from genesis. For a log that grows to ~50K entries per year, full-chain verification takes a few seconds.
- Any corruption (bit rot, disk error) in a single entry breaks the chain from that point forward. This is intentional (detects corruption) but requires a recovery procedure (backup + restore from last known good state).
- `canonical_json()` must be deterministic. Field ordering, float formatting, and null handling must be consistent. Any change to canonical serialization breaks the chain.

**References:** spec/Architecture.md §1.8 (audit + debug streams), §5.5 (error code registry), §16 (anti-pattern: must use `canonical_json`), spec/Source.md §4.1 (trust contract).
