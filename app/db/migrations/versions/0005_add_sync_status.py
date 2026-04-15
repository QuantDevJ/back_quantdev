"""Add sync_status to plaid_connections

Revision ID: 0005_add_sync_status
Revises: 0004_transactions_sync_support
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_add_sync_status"
down_revision = "0004_transactions_sync_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the sync_status enum type
    sync_status_enum = sa.Enum("pending", "syncing", "completed", "failed", name="sync_status")
    sync_status_enum.create(op.get_bind(), checkfirst=True)

    # Add sync_status column to plaid_connections
    op.add_column(
        "plaid_connections",
        sa.Column(
            "sync_status",
            sa.Enum("pending", "syncing", "completed", "failed", name="sync_status"),
            server_default="pending",
            nullable=False,
        ),
    )

    # Update existing rows: if last_sync_at is set, mark as completed; otherwise pending
    op.execute("""
        UPDATE plaid_connections
        SET sync_status = CASE
            WHEN last_sync_at IS NOT NULL THEN 'completed'::sync_status
            WHEN last_sync_error IS NOT NULL THEN 'failed'::sync_status
            ELSE 'pending'::sync_status
        END
    """)


def downgrade() -> None:
    # Drop the sync_status column
    op.drop_column("plaid_connections", "sync_status")

    # Drop the enum type
    sa.Enum(name="sync_status").drop(op.get_bind(), checkfirst=True)
