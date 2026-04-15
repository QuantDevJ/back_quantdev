"""Snapshot generation service for performance timeline tracking.

This module provides services to create and query performance snapshots
for holdings, enabling historical performance tracking and analytics.
"""

import logging
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Holding, PerformanceSnapshot

logger = logging.getLogger(__name__)


class SnapshotService:
    """Service for creating and querying performance snapshots.

    Performance snapshots capture the state of a holding at a specific
    point in time, enabling timeline views and historical analysis.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_snapshot_for_holding(
        self,
        holding: Holding,
        snapshot_date: Optional[date] = None,
    ) -> Optional[PerformanceSnapshot]:
        """Create a performance snapshot for a single holding.

        Calculates value, cost basis, unrealized gain, and gain percentage.
        Uses upsert logic via unique constraint to prevent duplicates.

        Args:
            holding: The Holding object to snapshot
            snapshot_date: Date for the snapshot (defaults to today)

        Returns:
            Created or updated PerformanceSnapshot, or None if skipped
        """
        if snapshot_date is None:
            snapshot_date = date.today()

        # Skip zero-quantity holdings
        if holding.quantity is None or holding.quantity == 0:
            logger.debug("Skipping snapshot for zero-quantity holding %s", holding.id)
            return None

        # Calculate snapshot values
        value = float(holding.current_value) if holding.current_value is not None else 0.0
        cost_basis_total = float(holding.cost_basis_total) if holding.cost_basis_total is not None else None

        unrealized_gain = None
        unrealized_gain_pct = None

        if cost_basis_total is not None and cost_basis_total != 0:
            unrealized_gain = value - cost_basis_total
            unrealized_gain_pct = (unrealized_gain / cost_basis_total) * 100
            # Cap percentage to fit in Numeric(6,2) column (-9999.99 to 9999.99)
            unrealized_gain_pct = max(-9999.99, min(9999.99, unrealized_gain_pct))

        # Use PostgreSQL upsert (INSERT ... ON CONFLICT)
        stmt = insert(PerformanceSnapshot).values(
            holding_id=holding.id,
            snapshot_date=snapshot_date,
            value=value,
            cost_basis_total=cost_basis_total,
            unrealized_gain=unrealized_gain,
            unrealized_gain_pct=unrealized_gain_pct,
        )

        # On conflict, update the values
        stmt = stmt.on_conflict_do_update(
            constraint="uq_perf_snapshot_holding_date",
            set_={
                "value": stmt.excluded.value,
                "cost_basis_total": stmt.excluded.cost_basis_total,
                "unrealized_gain": stmt.excluded.unrealized_gain,
                "unrealized_gain_pct": stmt.excluded.unrealized_gain_pct,
            },
        )

        self.db.execute(stmt)
        self.db.flush()

        # Fetch the upserted snapshot
        snapshot = (
            self.db.query(PerformanceSnapshot)
            .filter(
                PerformanceSnapshot.holding_id == holding.id,
                PerformanceSnapshot.snapshot_date == snapshot_date,
            )
            .first()
        )

        return snapshot

    def create_snapshots_for_holdings(
        self,
        holdings: list[Holding],
        snapshot_date: Optional[date] = None,
    ) -> int:
        """Create snapshots for multiple holdings in bulk.

        Args:
            holdings: List of Holding objects to snapshot
            snapshot_date: Date for all snapshots (defaults to today)

        Returns:
            Number of snapshots created/updated
        """
        if snapshot_date is None:
            snapshot_date = date.today()

        count = 0
        for holding in holdings:
            snapshot = self.create_snapshot_for_holding(holding, snapshot_date)
            if snapshot is not None:
                count += 1

        logger.info("Created/updated %d snapshots for date %s", count, snapshot_date)
        return count

    def get_holding_history(
        self,
        holding_id: UUID,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 365,
    ) -> list[PerformanceSnapshot]:
        """Get performance history for a holding.

        Args:
            holding_id: ID of the holding
            start_date: Start of date range (inclusive)
            end_date: End of date range (inclusive)
            limit: Maximum number of snapshots to return

        Returns:
            List of PerformanceSnapshot ordered by date descending
        """
        query = self.db.query(PerformanceSnapshot).filter(
            PerformanceSnapshot.holding_id == holding_id
        )

        if start_date is not None:
            query = query.filter(PerformanceSnapshot.snapshot_date >= start_date)

        if end_date is not None:
            query = query.filter(PerformanceSnapshot.snapshot_date <= end_date)

        snapshots = (
            query.order_by(PerformanceSnapshot.snapshot_date.desc())
            .limit(limit)
            .all()
        )

        return snapshots

    def get_date_range_for_holding(self, holding_id: UUID) -> tuple[Optional[date], Optional[date]]:
        """Get the earliest and latest snapshot dates for a holding.

        Args:
            holding_id: ID of the holding

        Returns:
            Tuple of (earliest_date, latest_date) or (None, None) if no snapshots
        """
        from sqlalchemy import func

        result = self.db.query(
            func.min(PerformanceSnapshot.snapshot_date),
            func.max(PerformanceSnapshot.snapshot_date),
        ).filter(
            PerformanceSnapshot.holding_id == holding_id
        ).first()

        if result:
            return result[0], result[1]
        return None, None
