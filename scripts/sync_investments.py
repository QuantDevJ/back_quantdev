#!/usr/bin/env python3
"""
Cron script to sync Plaid investments for all active connections.

Environment variables:
    SYNC_INTERVAL_HOURS: Hours between syncs (default: 24)

Usage:
    python scripts/sync_investments.py

Cron example (daily at midnight):
    0 0 * * * cd /path/to/quantly-backend && /path/to/venv/bin/python scripts/sync_investments.py >> /var/log/quantly_sync.log 2>&1
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import PlaidConnection, PlaidConnectionStatus
from app.plaid.service import PlaidService

# Get sync interval from environment (default: 24 hours)
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "24"))


def sync_all_connections(db: Session, hours_threshold: int = SYNC_INTERVAL_HOURS) -> dict:
    """Sync all active connections that haven't been synced in the last N hours."""
    # Use naive UTC datetime for database comparison (DB stores naive UTC)
    threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_threshold)

    # Find active connections that need syncing
    connections = (
        db.query(PlaidConnection)
        .filter(
            PlaidConnection.status == PlaidConnectionStatus.active,
            (PlaidConnection.last_sync_at.is_(None)) | (PlaidConnection.last_sync_at < threshold),
        )
        .all()
    )

    print(f"[{datetime.now(timezone.utc).isoformat()}] Found {len(connections)} connections to sync")

    results = {
        "total": len(connections),
        "success": 0,
        "failed": 0,
        "errors": [],
    }

    service = PlaidService(db)

    for conn in connections:
        try:
            print(f"  Syncing connection {conn.id} ({conn.institution_name})...")
            result = service.ingest_investments(
                user_id=conn.user_id,
                connection_id=conn.id,
            )
            print(f"    Success: {result}")
            results["success"] += 1
        except Exception as e:
            error_msg = f"Connection {conn.id}: {str(e)}"
            print(f"    Failed: {error_msg}")
            results["failed"] += 1
            results["errors"].append(error_msg)

            # Update connection status if it's a persistent error
            if "ITEM_LOGIN_REQUIRED" in str(e):
                conn.status = PlaidConnectionStatus.pending_refresh
                conn.last_sync_error = str(e)
                db.commit()

    return results


def main():
    print(f"\n{'='*60}")
    print(f"Plaid Investment Sync - {datetime.now(timezone.utc).isoformat()}")
    print(f"Sync interval: {SYNC_INTERVAL_HOURS} hours")
    print(f"{'='*60}\n")

    db = SessionLocal()
    try:
        results = sync_all_connections(db)
        print(f"\n{'='*60}")
        print(f"Sync Complete: {results['success']}/{results['total']} successful")
        if results["failed"] > 0:
            print(f"Failed: {results['failed']}")
            for err in results["errors"]:
                print(f"  - {err}")
        print(f"{'='*60}\n")

        # Exit with error code if any failed
        sys.exit(1 if results["failed"] > 0 else 0)
    finally:
        db.close()


if __name__ == "__main__":
    main()
