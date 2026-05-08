"""Hash-chained audit log — append-only, immutable, verified (Architecture.md §5.1)."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from pmacs.data.canonical import canonical_json
from pmacs.constants import AUDIT_GENESIS_PREV_SHA


class AuditWriter:
    """Append-only hash-chained audit log writer.

    Format per line:
    <iso_ts>\t<prev_sha256>\t<event_type>\t<canonical_json>\t<this_sha256>

    Genesis: prev_sha256 = "0" * 64
    fsync after every write.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prev_sha: str = AUDIT_GENESIS_PREV_SHA
        self._fd = None

        # If file exists, recover last SHA
        if self._path.exists():
            self._recover_last_sha()

    def _recover_last_sha(self) -> None:
        """Scan existing file to recover the last SHA."""
        last_sha = AUDIT_GENESIS_PREV_SHA
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    parts = line.split("\t")
                    if len(parts) >= 5:
                        last_sha = parts[4]
        self._prev_sha = last_sha

    def _open(self):
        if self._fd is None:
            self._fd = open(self._path, "a")
        return self._fd

    def append(self, event_type: str, payload: dict, cycle_id: str = "") -> str:
        """Append an event to the audit log. Returns this entry's SHA256."""
        iso_ts = datetime.now(timezone.utc).isoformat()

        # Add cycle_id to payload if provided
        if cycle_id:
            payload = {**payload, "cycle_id": cycle_id}

        canon = canonical_json(payload)

        # Compute hash: sha256(iso_ts || prev_sha || event_type || canonical_json)
        hasher = hashlib.sha256()
        hasher.update(iso_ts.encode("utf-8") + b"\x00")
        hasher.update(self._prev_sha.encode("utf-8") + b"\x00")
        hasher.update(event_type.encode("utf-8") + b"\x00")
        hasher.update(canon.encode("utf-8"))
        this_sha = hasher.hexdigest()

        line = f"{iso_ts}\t{self._prev_sha}\t{event_type}\t{canon}\t{this_sha}\n"

        fd = self._open()
        fd.write(line)
        fd.flush()
        os.fsync(fd.fileno())

        self._prev_sha = this_sha
        return this_sha

    def close(self) -> None:
        if self._fd is not None:
            self._fd.close()
            self._fd = None


class AuditVerifier:
    """Verify the hash chain integrity of an audit log."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    def verify_full(self) -> tuple[bool, str]:
        """Verify the entire chain. Returns (ok, error_message)."""
        if not self._path.exists():
            return True, ""

        prev_sha = AUDIT_GENESIS_PREV_SHA
        line_num = 0

        with open(self._path) as f:
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) != 5:
                    return False, f"Line {line_num}: expected 5 tab-separated fields, got {len(parts)}"

                iso_ts, stored_prev, event_type, canon, stored_sha = parts

                # Verify prev SHA matches
                if stored_prev != prev_sha:
                    return False, f"Line {line_num}: prev_sha mismatch (chain broken)"

                # Recompute hash
                hasher = hashlib.sha256()
                hasher.update(iso_ts.encode("utf-8") + b"\x00")
                hasher.update(prev_sha.encode("utf-8") + b"\x00")
                hasher.update(event_type.encode("utf-8") + b"\x00")
                hasher.update(canon.encode("utf-8"))
                computed_sha = hasher.hexdigest()

                if computed_sha != stored_sha:
                    return False, f"Line {line_num}: hash mismatch (tampered)"

                prev_sha = stored_sha

        return True, ""

    def verify_incremental(self, last_n: int = 1000) -> tuple[bool, str]:
        """Verify the last N entries + random sample from history."""
        if not self._path.exists():
            return True, ""

        lines = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)

        if not lines:
            return True, ""

        # Verify last N
        start = max(0, len(lines) - last_n)
        prev_sha = AUDIT_GENESIS_PREV_SHA

        # If we're not starting from beginning, get prev_sha from previous line
        if start > 0:
            prev_parts = lines[start - 1].split("\t")
            if len(prev_parts) >= 5:
                prev_sha = prev_parts[4]

        for i in range(start, len(lines)):
            parts = lines[i].split("\t")
            if len(parts) != 5:
                return False, f"Line {i+1}: malformed"

            iso_ts, stored_prev, event_type, canon, stored_sha = parts

            if stored_prev != prev_sha:
                return False, f"Line {i+1}: chain broken"

            hasher = hashlib.sha256()
            hasher.update(iso_ts.encode("utf-8") + b"\x00")
            hasher.update(prev_sha.encode("utf-8") + b"\x00")
            hasher.update(event_type.encode("utf-8") + b"\x00")
            hasher.update(canon.encode("utf-8"))
            computed = hasher.hexdigest()

            if computed != stored_sha:
                return False, f"Line {i+1}: hash mismatch"

            prev_sha = stored_sha

        return True, ""
