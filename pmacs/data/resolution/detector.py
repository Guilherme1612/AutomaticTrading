"""Catalyst resolution detector -- identifies resolved catalysts (Architecture.md §7, §9 step 7).

Wraps the multi-source corroboration engine for use by the orchestrator.
Maintains backward compatibility with the run_all(db_path) signature
while supporting a richer interface with explicit catalyst/evidence inputs.
"""
from __future__ import annotations

import json
import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.catalysts import Catalyst, CatalystStatus, CatalystType
from pmacs.schemas.data import DataSource, Evidence, EvidenceType
from pmacs.schemas.resolution import ResolutionResult


class CatalystResolutionDetector:
    """Detect resolved catalysts from pending catalyst records.

    Queries the SQLite database for pending catalysts, matches them against
    available evidence using multi-source corroboration (Arch §7.2), and
    returns resolution results.

    Usage:
        # Legacy (orchestrator) -- queries DB for pending catalysts + evidence
        detector = CatalystResolutionDetector()
        resolved = detector.run_all(db_path)

        # Rich interface -- explicit inputs (for testing / direct calls)
        resolved = detector.run_all(
            db_path,
            pending_catalysts=catalysts,
            evidence=evidence_list,
            price_before=150.0,
            price_after=155.0,
            cycle_id="2026-05-17T10:00:00",
        )
    """

    def run_all(
        self,
        db_path: Path,
        pending_catalysts: list[Catalyst] | None = None,
        evidence: list[Evidence] | None = None,
        price_before: float | None = None,
        price_after: float | None = None,
        cycle_id: str = "",
    ) -> list[dict]:
        """Detect resolved catalysts using multi-source corroboration.

        Args:
            db_path: Path to SQLite database.
            pending_catalysts: If provided, use these instead of querying DB.
            evidence: If provided, use these instead of querying DB.
            price_before: Price before resolution window.
            price_after: Price after resolution window.
            cycle_id: Cycle ID for logging.

        Returns:
            List of resolved catalyst dicts (backward-compatible format).
        """
        try:
            # Load from DB if not provided
            if pending_catalysts is None:
                pending_catalysts = self._load_pending_catalysts(db_path)

            if evidence is None:
                evidence = self._load_recent_evidence(db_path)

            if not pending_catalysts:
                return []

            # Run detection
            from pmacs.data.resolution.catalyst_detector import detect_resolutions

            # Group catalysts by ticker
            by_ticker: dict[str, list[Catalyst]] = {}
            for c in pending_catalysts:
                by_ticker.setdefault(c.ticker, []).append(c)

            all_results: list[ResolutionResult] = []
            for ticker, catalysts in by_ticker.items():
                results = detect_resolutions(
                    ticker=ticker,
                    pending_catalysts=catalysts,
                    evidence=evidence,
                    price_before=price_before,
                    price_after=price_after,
                    cycle_id=cycle_id,
                )
                all_results.extend(results)

            # Persist resolution results to DB
            if all_results:
                self._persist_resolutions(db_path, all_results, cycle_id)

            # Return as dicts for backward compatibility
            return [r.model_dump(mode="json") for r in all_results]

        except Exception as exc:
            log_debug(
                "CATALYST_RESOLUTION_FATAL",
                payload={"error": str(exc), "cycle_id": cycle_id},
                level="WARN",
                error_code="CATALYST_RESOLUTION_ERROR",
                cycle_id=cycle_id or "",
                msg=f"Catalyst resolution failed: {exc}",
            )
            return []

    def _load_pending_catalysts(self, db_path: Path) -> list[Catalyst]:
        """Load pending/confirmed catalysts from SQLite."""
        catalysts: list[Catalyst] = []
        try:
            conn = _sql_connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT id, ticker, type, status, expected_date, actual_date, "
                    "description, source_urls, confidence, detected_at "
                    "FROM catalysts "
                    "WHERE status IN ('PENDING', 'CONFIRMED') "
                    "ORDER BY expected_date ASC"
                ).fetchall()

                for row in rows:
                    source_urls = json.loads(row[7]) if row[7] else []
                    detected_at = row[9]
                    if isinstance(detected_at, str):
                        detected_at = datetime.fromisoformat(detected_at)

                    catalysts.append(Catalyst(
                        id=row[0],
                        ticker=row[1],
                        type=CatalystType(row[2]),
                        status=CatalystStatus(row[3]),
                        expected_date=row[4],
                        actual_date=row[5],
                        description=row[6] or "",
                        source_urls=source_urls,
                        confidence=row[8] if row[8] is not None else 0.5,
                        detected_at=detected_at if detected_at else datetime.now(timezone.utc),
                    ))
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # Table may not exist yet (pre-bootstrap)
            pass
        return catalysts

    def _load_recent_evidence(self, db_path: Path) -> list[Evidence]:
        """Load recent evidence from SQLite."""
        evidence_list: list[Evidence] = []
        try:
            conn = _sql_connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT id, source, type, ticker, fetched_at, content_hash, "
                    "data, url, title, published_at "
                    "FROM evidence "
                    "WHERE fetched_at > datetime('now', '-7 days') "
                    "ORDER BY fetched_at DESC"
                ).fetchall()

                for row in rows:
                    data = json.loads(row[6]) if row[6] else {}
                    fetched_at = row[4]
                    if isinstance(fetched_at, str):
                        fetched_at = datetime.fromisoformat(fetched_at)
                    published_at = row[9]
                    if isinstance(published_at, str):
                        published_at = datetime.fromisoformat(published_at)

                    evidence_list.append(Evidence(
                        id=row[0],
                        source=DataSource(row[1]),
                        type=EvidenceType(row[2]),
                        ticker=row[3],
                        fetched_at=fetched_at if fetched_at else datetime.now(timezone.utc),
                        content_hash=row[5] or "",
                        data=data,
                        url=row[7],
                        title=row[8],
                        published_at=published_at,
                    ))
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # Table may not exist yet (pre-bootstrap)
            pass
        return evidence_list

    def _persist_resolutions(
        self,
        db_path: Path,
        results: list[ResolutionResult],
        cycle_id: str,
    ) -> None:
        """Write resolution results back to SQLite."""
        try:
            conn = _sql_connect(db_path)
            try:
                for r in results:
                    # Update catalyst status
                    conn.execute(
                        "UPDATE catalysts SET status = ?, actual_date = ? WHERE id = ?",
                        (r.new_status.value, date.today().isoformat(), r.catalyst_id),
                    )

                    # Insert resolution record
                    conn.execute(
                        "INSERT OR REPLACE INTO resolutions "
                        "(id, catalyst_id, ticker, old_status, new_status, "
                        " resolved_at, corroboration_tier, confidence, "
                        " supporting_evidence_ids, price_change_pct, summary, cycle_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            f"res-{r.catalyst_id}",
                            r.catalyst_id,
                            r.ticker,
                            r.old_status.value,
                            r.new_status.value,
                            r.resolved_at.isoformat(),
                            r.corroboration_tier,
                            r.confidence,
                            json.dumps(r.supporting_evidence_ids),
                            r.price_change_pct,
                            r.summary,
                            cycle_id,
                        ),
                    )

                conn.commit()
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            log_debug(
                "CATALYST_RESOLUTION_PERSIST_FAILED",
                payload={"error": str(exc), "count": len(results)},
                level="WARN",
                error_code="DB_WRITE_FAILED",
                cycle_id=cycle_id or "",
                msg=f"Failed to persist {len(results)} resolution results: {exc}",
            )
