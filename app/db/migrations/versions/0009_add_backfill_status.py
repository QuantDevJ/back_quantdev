"""Add backfill status fields to plaid_connections

Revision ID: 0009_add_backfill_status
Revises: 0008_add_account_snapshots
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_add_backfill_status"
down_revision = "0008_add_account_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the backfill_status enum type
    backfill_status_enum = sa.Enum(
        "pending", "in_progress", "completed", "failed",
        name="backfill_status"
    )
    backfill_status_enum.create(op.get_bind(), checkfirst=True)

    # Add backfill status columns to plaid_connections
    op.add_column(
        "plaid_connections",
        sa.Column(
            "backfill_status",
            sa.Enum("pending", "in_progress", "completed", "failed", name="backfill_status"),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "plaid_connections",
        sa.Column("backfill_started_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "plaid_connections",
        sa.Column("backfill_completed_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "plaid_connections",
        sa.Column("backfill_error", sa.Text, nullable=True),
    )
    op.add_column(
        "plaid_connections",
        sa.Column("backfill_snapshots_created", sa.Integer, server_default="0", nullable=False),
    )


def downgrade() -> None:
    # Drop columns
    op.drop_column("plaid_connections", "backfill_snapshots_created")
    op.drop_column("plaid_connections", "backfill_error")
    op.drop_column("plaid_connections", "backfill_completed_at")
    op.drop_column("plaid_connections", "backfill_started_at")
    op.drop_column("plaid_connections", "backfill_status")

    # Drop the enum type
    sa.Enum(name="backfill_status").drop(op.get_bind(), checkfirst=True)
