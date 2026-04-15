"""Add indexes for performance snapshot queries

Revision ID: 0006_snapshot_indexes
Revises: 0005_add_sync_status
Create Date: 2026-04-14
"""

from alembic import op

revision = "0006_snapshot_indexes"
down_revision = "0005_add_sync_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add composite index on (holding_id, snapshot_date DESC) for efficient timeline queries
    # This index supports queries that fetch snapshots for a holding ordered by date
    op.create_index(
        "ix_perf_snapshots_holding_date_desc",
        "performance_snapshots",
        ["holding_id", "snapshot_date"],
        postgresql_ops={"snapshot_date": "DESC"},
    )

    # Add index on snapshot_date for date-range queries across all holdings
    op.create_index(
        "ix_perf_snapshots_date",
        "performance_snapshots",
        ["snapshot_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_perf_snapshots_date", table_name="performance_snapshots")
    op.drop_index("ix_perf_snapshots_holding_date_desc", table_name="performance_snapshots")
