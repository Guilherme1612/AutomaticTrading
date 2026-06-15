#!/usr/bin/env python3
"""ONDS Research Script — Run single-ticker analysis cycles and compare results.

Usage:
    python scripts/onds_research.py clear      # Clear ONDS data from SQLite
    python scripts/onds_research.py run         # Run one ONDS-only cycle
    python scripts/onds_research.py compare     # Compare all recorded runs
    python scripts/onds_research.py status      # Show current ONDS data
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pmacs.config import data_dir as _get_data_dir

DATA_DIR = _get_data_dir()
DB_PATH = DATA_DIR / "pmacs.db"
AUDIT_PATH = DATA_DIR / "audit.log"
RESULTS_FILE = DATA_DIR / "onds_research_runs.json"


def _load_runs() -> list[dict]:
    if RESULTS_FILE.exists():
        return json.loads(RESULTS_FILE.read_text())
    return []


def _save_runs(runs: list[dict]) -> None:
    RESULTS_FILE.write_text(json.dumps(runs, indent=2))


def cmd_status():
    """Show current ONDS data."""
    print("=== ONDS Current Status ===\n")

    # Audit log decisions
    decisions = []
    if AUDIT_PATH.exists():
        with open(AUDIT_PATH) as f:
            for line in f:
                if '"ticker":"ONDS"' in line and 'DECISION' in line:
                    parts = line.strip().split('\t')
                    if len(parts) >= 4:
                        data = json.loads(parts[3])
                        decisions.append({
                            'ts': parts[0][:19],
                            'conv': data.get('conviction', 0),
                            'verdict': data.get('verdict', '?'),
                            'price': data.get('price', 0),
                        })

    print(f"Audit log decisions: {len(decisions)}")
    if decisions:
        verdicts = {}
        convictions = []
        for d in decisions:
            verdicts[d['verdict']] = verdicts.get(d['verdict'], 0) + 1
            convictions.append(d['conv'])
        print(f"  Verdicts: {verdicts}")
        print(f"  Conviction: {min(convictions):.4f} to {max(convictions):.4f} (avg {sum(convictions)/len(convictions):.4f})")
        print(f"\n  Last 5:")
        for d in decisions[-5:]:
            print(f"    {d['ts']}  conv={d['conv']:+.4f}  {d['verdict']:5}  ${d['price']:.2f}")

    # SQLite state
    conn = sqlite3.connect(str(DB_PATH))
    for table in ['decisions', 'memos', 'queue', 'evidence_cache']:
        count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ticker='ONDS'").fetchone()[0]
        print(f"\n  {table}: {count} rows")
    conn.close()

    # Saved runs
    runs = _load_runs()
    print(f"\n  Saved research runs: {len(runs)}")


def cmd_clear():
    """Clear ALL ONDS data from SQLite tables (not audit log — that's immutable)."""
    print("=== Clearing ONDS Data ===\n")

    conn = sqlite3.connect(str(DB_PATH))
    tables = [
        'decisions', 'memos', 'queue', 'holdings', 'stop_events',
        'scan_records', 'failure_classifications', 'evidence_cache',
        'operator_overrides', 'persistent_pins',
    ]
    total_deleted = 0
    for table in tables:
        try:
            cursor = conn.execute(f"DELETE FROM {table} WHERE ticker='ONDS'")
            if cursor.rowcount > 0:
                print(f"  {table}: deleted {cursor.rowcount} rows")
                total_deleted += cursor.rowcount
            else:
                print(f"  {table}: 0 rows (clean)")
        except sqlite3.OperationalError as e:
            print(f"  {table}: skip ({e})")

    conn.commit()
    conn.close()
    print(f"\n  Total deleted: {total_deleted} rows")
    print("  Note: Audit log is immutable — historical decisions preserved there.")


def cmd_run():
    """Run a single ONDS-only cycle using the CycleOrchestrator."""
    from pmacs.nervous.orchestrator import CycleOrchestrator
    from pmacs.nervous.sse_publisher import SSEPublisher
    from pmacs.storage.sqlite import connect as _sql_connect

    run_number = len(_load_runs()) + 1
    print(f"=== ONDS Research Run #{run_number} ===\n")

    # Record pre-run state (count existing ONDS decisions)
    pre_count = 0
    if AUDIT_PATH.exists():
        with open(AUDIT_PATH) as f:
            for line in f:
                if '"ticker":"ONDS"' in line and 'DECISION' in line:
                    pre_count += 1

    # Temporarily set universe to ONDS only by patching the queue composition
    publisher = SSEPublisher()
    orch = CycleOrchestrator(
        db_path=DB_PATH,
        audit_path=AUDIT_PATH,
        sse_publisher=publisher,
        config={
            "lock_path": str(DATA_DIR / "onds_research.lock"),
        },
    )

    # Monkey-patch to only process ONDS
    original_queue_composition = orch._step_queue_composition

    def _onds_only_queue(cycle_id: str) -> None:
        """Override queue to contain only ONDS."""
        from pmacs.schemas.queue import QueueItem, PriorityBand
        now = datetime.now(timezone.utc).isoformat()
        queue = [QueueItem(
            cycle_id=cycle_id,
            ticker="ONDS",
            priority_band=PriorityBand.P1_HIGH,
            pinned=True,
            enqueued_at=now,
        )]
        # Write to SQLite
        conn = _sql_connect(DB_PATH)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO queue "
                "(cycle_id, ticker, priority_band, pinned, enqueued_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (cycle_id, "ONDS", 1, 1, now),
            )
            conn.commit()
        finally:
            conn.close()

        orch._queue = queue
        print(f"  Queue: ONDS only (P1, pinned)")

    orch._step_queue_composition = _onds_only_queue

    start_time = time.time()
    print(f"  Starting cycle at {datetime.now().strftime('%H:%M:%S')}...")
    print(f"  (This will take 2-5 minutes depending on inference speed)\n")

    try:
        cycle_id = orch.run_cycle("ONDS_RESEARCH")
        elapsed = time.time() - start_time
        print(f"\n  Cycle completed: {cycle_id} ({elapsed:.1f}s)")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n  Cycle FAILED after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        return

    # Extract the ONDS decision from this cycle
    result = None
    if AUDIT_PATH.exists():
        with open(AUDIT_PATH) as f:
            for line in f:
                if '"ticker":"ONDS"' in line and 'DECISION' in line and cycle_id in line:
                    parts = line.strip().split('\t')
                    if len(parts) >= 4:
                        result = json.loads(parts[3])

    if result:
        print(f"\n  === Result ===")
        print(f"  Verdict:    {result.get('verdict')}")
        print(f"  Conviction: {result.get('conviction', 0):.4f}")
        print(f"  Price:      ${result.get('price', 0):.2f}")
    else:
        print(f"\n  WARNING: No ONDS decision found for {cycle_id}")

    # Also check for memo
    conn = sqlite3.connect(str(DB_PATH))
    memo_row = conn.execute(
        "SELECT memo_json, raw_text FROM memos WHERE ticker='ONDS' AND cycle_id=?",
        (cycle_id,)
    ).fetchone()
    memo_data = None
    if memo_row:
        memo_data = json.loads(memo_row[0]) if memo_row[0] else None
        print(f"  Memo:       YES ({len(memo_row[1] or '')} chars)")
    else:
        print(f"  Memo:       NO")
    conn.close()

    # Save run result
    run_entry = {
        "run_number": run_number,
        "cycle_id": cycle_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(elapsed, 1),
        "verdict": result.get('verdict') if result else None,
        "conviction": result.get('conviction') if result else None,
        "price": result.get('price') if result else None,
        "memo_available": memo_row is not None,
        "memo_text": memo_row[1][:2000] if memo_row and memo_row[1] else None,
    }
    runs = _load_runs()
    runs.append(run_entry)
    _save_runs(runs)
    print(f"\n  Run #{run_number} saved to {RESULTS_FILE.name}")


def cmd_compare():
    """Compare all recorded runs."""
    runs = _load_runs()
    if not runs:
        print("No runs recorded yet. Use 'run' first.")
        return

    print(f"=== ONDS Research Comparison ({len(runs)} runs) ===\n")
    print(f"{'Run':>4}  {'Verdict':>8}  {'Conviction':>10}  {'Price':>8}  {'Time':>7}  {'Memo':>5}  Cycle ID")
    print("-" * 85)

    best_run = None
    best_conv = -1

    for r in runs:
        conv = r.get('conviction', 0) or 0
        has_memo = "YES" if r.get('memo_available') else "NO"
        verdict = r.get('verdict', '?')
        print(f"  #{r['run_number']:>2}  {verdict:>8}  {conv:>+10.4f}  ${r.get('price', 0):>7.2f}  {r.get('elapsed_s', 0):>5.1f}s  {has_memo:>5}  {r.get('cycle_id', '?')}")

        if conv > best_conv:
            best_conv = conv
            best_run = r

    print(f"\n  Best run: #{best_run['run_number']} — {best_run.get('verdict')} conv={best_conv:.4f}")

    # Show memo snippets if available
    for r in runs:
        if r.get('memo_text'):
            print(f"\n--- Run #{r['run_number']} Memo Snippet ---")
            print(r['memo_text'][:500])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "status":
        cmd_status()
    elif cmd == "clear":
        cmd_clear()
    elif cmd == "run":
        cmd_run()
    elif cmd == "compare":
        cmd_compare()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
