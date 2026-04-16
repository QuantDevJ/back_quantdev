"""Add account_snapshots table for balance history tracking

Revision ID: 0008_add_account_snapshots
Revises: 0007_add_snapshot_constraint
Create Date: 2026-04-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_add_account_snapshots"
down_revision = "0007_add_snapshot_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_snapshots",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("current_balance", sa.Numeric(15, 2), nullable=False),
        sa.Column("available_balance", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "snapshot_date", name="uq_account_snapshot_account_date"),
    )

    # Add composite index for efficient timeline queries
    op.create_index(
        "ix_account_snapshots_account_date_desc",
        "account_snapshots",
        ["account_id", "snapshot_date"],
        postgresql_ops={"snapshot_date": "DESC"},
    )

    # Add index on snapshot_date for date-range queries
    op.create_index(
        "ix_account_snapshots_date",
        "account_snapshots",
        ["snapshot_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_snapshots_date", table_name="account_snapshots")
    op.drop_index("ix_account_snapshots_account_date_desc", table_name="account_snapshots")
    op.drop_table("account_snapshots")
