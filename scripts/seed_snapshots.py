"""Seed script to generate dummy performance snapshots for testing the timeline chart.

Run with: python scripts/seed_snapshots.py
"""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import Holding, PerformanceSnapshot


def get_db_url():
    return f"postgresql+psycopg://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"


def generate_snapshots(db, holding: Holding, days: int = 30):
    """Generate dummy snapshots for a holding over the past N days."""
    today = date.today()

    # Get initial values from the holding
    base_value = float(holding.current_value) if holding.current_value else 100.0
    cost_basis = float(holding.cost_basis_total) if holding.cost_basis_total else base_value * 0.9

    # Generate data with some realistic variation
    snapshots_created = 0

    for i in range(days, 0, -1):
        snapshot_date = today - timedelta(days=i)

        # Skip weekends for more realistic market data
        if snapshot_date.weekday() >= 5:
            continue

        # Calculate value with random walk (slight upward bias)
        # Start from a lower value and trend towards current value
        progress = (days - i) / days
        base_for_day = cost_basis + (base_value - cost_basis) * progress

        # Add random daily variation (-3% to +3%)
        variation = random.uniform(-0.03, 0.035)
        value = base_for_day * (1 + variation)
        value = round(value, 2)

        # Calculate unrealized gain
        unrealized_gain = value - cost_basis
        unrealized_gain_pct = (unrealized_gain / cost_basis * 100) if cost_basis != 0 else 0
        unrealized_gain_pct = max(-9999.99, min(9999.99, unrealized_gain_pct))

        # Check if snapshot already exists
        existing = db.query(PerformanceSnapshot).filter(
            PerformanceSnapshot.holding_id == holding.id,
            PerformanceSnapshot.snapshot_date == snapshot_date,
        ).first()

        if existing:
            # Update existing
            existing.value = value
            existing.cost_basis_total = cost_basis
            existing.unrealized_gain = round(unrealized_gain, 2)
            existing.unrealized_gain_pct = round(unrealized_gain_pct, 2)
        else:
            # Create new
            snapshot = PerformanceSnapshot(
                holding_id=holding.id,
                snapshot_date=snapshot_date,
                value=value,
                cost_basis_total=cost_basis,
                unrealized_gain=round(unrealized_gain, 2),
                unrealized_gain_pct=round(unrealized_gain_pct, 2),
            )
            db.add(snapshot)
            snapshots_created += 1

    return snapshots_created


def main():
    print("Connecting to database...")
    engine = create_engine(get_db_url())
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Get all holdings
        holdings = db.query(Holding).all()
        print(f"Found {len(holdings)} holdings")

        if not holdings:
            print("No holdings found. Connect a Plaid account first.")
            return

        total_created = 0
        for holding in holdings:
            print(f"Generating snapshots for holding {holding.id}...")
            created = generate_snapshots(db, holding, days=60)
            total_created += created
            print(f"  Created {created} snapshots")

        db.commit()
        print(f"\nDone! Created {total_created} total snapshots.")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
