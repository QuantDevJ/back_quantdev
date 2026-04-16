"""APScheduler setup for scheduled investment sync and snapshot creation.

This module provides a 24-hour cron job that automatically:
1. Syncs investment data from Plaid for all active connections
2. Creates holding snapshots (performance tracking)
3. Creates account snapshots (balance history)
4. Runs historical backfill jobs asynchronously

Transaction sync is NOT included - it's manual + webhook only.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import PlaidConnection

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def sync_investments_job() -> None:
    """Cron job: Sync investment data for all active connections and create snapshots.

    This job runs every 24 hours (configurable) and:
    1. Fetches latest investment data from Plaid
    2. Creates/updates holding snapshots
    3. Creates/updates account snapshots

    Note: Transaction sync is NOT included - it's manual + webhook only.
    """
    logger.info("Starting scheduled investment sync for all connections")

    db = SessionLocal()
    try:
        # Get all active connections
        connections = (
            db.query(PlaidConnection)
            .filter(PlaidConnection.status == "active")
            .all()
        )

        if not connections:
            logger.info("No active connections found for scheduled sync")
            return

        # Import here to avoid circular imports
        from app.plaid.service import PlaidService

        plaid_service = PlaidService(db)
        success_count = 0
        error_count = 0

        for connection in connections:
            try:
                # Sync investments only (creates holding + account snapshots)
                result = plaid_service.ingest_investments(
                    user_id=connection.user_id,
                    connection_id=connection.id,
                )
                logger.info(
                    "Synced investments for connection %s: %d holdings, %d account snapshots",
                    connection.id,
                    result.get("snapshots_created", 0),
                    result.get("account_snapshots_created", 0),
                )
                success_count += 1
            except Exception as e:
                logger.error("Failed to sync connection %s: %s", connection.id, e)
                error_count += 1

        db.commit()
        logger.info(
            "Completed scheduled investment sync: %d successful, %d failed",
            success_count,
            error_count,
        )
    except Exception as e:
        logger.exception("Unexpected error during scheduled sync: %s", e)
        db.rollback()
    finally:
        db.close()


def start_scheduler() -> None:
    """Start the scheduler if enabled via SNAPSHOT_CRON_ENABLED env var."""
    logger.info(
        "Scheduler config: SNAPSHOT_CRON_ENABLED=%s, SYNC_INTERVAL_HOURS=%s",
        settings.snapshot_cron_enabled,
        settings.sync_interval_hours,
    )
    if not settings.snapshot_cron_enabled:
        logger.info("Snapshot cron job is disabled (SNAPSHOT_CRON_ENABLED=false)")
        return

    # Schedule recurring job every N hours
    scheduler.add_job(
        sync_investments_job,
        trigger=IntervalTrigger(hours=settings.sync_interval_hours),
        id="investment_sync_job",
        name="Sync investments and create snapshots",
        replace_existing=True,
    )

    # Also run once on startup after a 10-second delay to let the app initialize
    scheduler.add_job(
        sync_investments_job,
        trigger="date",
        run_date=datetime.now() + timedelta(seconds=10),
        id="investment_sync_job_startup",
        name="Initial sync on startup",
        replace_existing=True,
    )
    logger.info("Scheduled initial sync to run in 10 seconds")

    scheduler.start()
    logger.info(
        "Scheduler started - running immediately, then every %d hours",
        settings.sync_interval_hours,
    )


def shutdown_scheduler() -> None:
    """Shutdown the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown complete")


async def run_backfill_job(connection_id: UUID) -> None:
    """Background job to run historical snapshot backfill.

    This job is triggered after a successful Plaid sync to create
    historical snapshots from transaction history.

    Args:
        connection_id: ID of the PlaidConnection to backfill
    """
    logger.info("Starting backfill job for connection %s", connection_id)

    db = SessionLocal()
    try:
        from app.plaid.historical_backfill import HistoricalBackfillService

        service = HistoricalBackfillService(db)
        result = service.backfill_connection(connection_id)
        logger.info(
            "Backfill job completed for connection %s: %s, %d snapshots",
            connection_id,
            result.status,
            result.snapshots_created,
        )
    except Exception as e:
        logger.exception("Backfill job failed for connection %s: %s", connection_id, e)
    finally:
        db.close()


def schedule_backfill_job(connection_id: UUID) -> None:
    """Schedule a background backfill job for a connection.

    This function is called after a successful Plaid sync to schedule
    the backfill to run asynchronously.

    Args:
        connection_id: ID of the PlaidConnection to backfill
    """
    # Add a one-time job to run the backfill with a small delay
    # to let the initial sync complete
    scheduler.add_job(
        run_backfill_job,
        trigger="date",
        run_date=datetime.now() + timedelta(seconds=5),
        args=[connection_id],
        id=f"backfill_{connection_id}",
        name=f"Backfill snapshots for connection {connection_id}",
        replace_existing=True,
    )
    logger.info("Scheduled backfill job for connection %s", connection_id)
