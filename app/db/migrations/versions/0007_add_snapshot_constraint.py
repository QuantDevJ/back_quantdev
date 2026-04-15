"""Add missing unique constraint for performance snapshots

Revision ID: 0007_add_snapshot_constraint
Revises: 0006_snapshot_indexes
Create Date: 2026-04-14
"""

from alembic import op

revision = "0007_add_snapshot_constraint"
down_revision = "0006_snapshot_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add unique constraint for upsert operations (required by model definition)
    # This was missing from the original table creation
    op.create_unique_constraint(
        "uq_perf_snapshot_holding_date",
        "performance_snapshots",
        ["holding_id", "snapshot_date"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_perf_snapshot_holding_date", "performance_snapshots", type_="unique")
