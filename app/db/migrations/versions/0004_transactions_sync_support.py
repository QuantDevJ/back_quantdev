"""Add transactions sync support with webhooks

Revision ID: 0004_transactions_sync_support
Revises: 0003_plaid_investment_columns
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_transactions_sync_support"
down_revision = "0003_plaid_investment_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add cursor columns to plaid_connections
    op.add_column("plaid_connections", sa.Column("transaction_cursor", sa.Text(), nullable=True))
    op.add_column("plaid_connections", sa.Column("transaction_cursor_updated_at", sa.DateTime(), nullable=True))

    # Add new enum values to account_type
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'checking'")
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'savings'")
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'money_market'")
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'cd'")
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'credit_card'")

    # Create banking_transactions table
    op.create_table(
        "banking_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plaid_transaction_id", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("iso_currency_code", sa.String(length=3), nullable=True),
        sa.Column("category", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("personal_finance_category", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("merchant_name", sa.String(length=500), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("authorized_date", sa.Date(), nullable=True),
        sa.Column("pending", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("payment_channel", sa.String(length=50), nullable=True),
        sa.Column("location_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plaid_transaction_id"),
    )

    # Create index on account_id and transaction_date
    op.create_index(
        "ix_banking_transactions_account_date",
        "banking_transactions",
        ["account_id", "transaction_date"],
    )


def downgrade() -> None:
    # Drop banking_transactions table
    op.drop_index("ix_banking_transactions_account_date", table_name="banking_transactions")
    op.drop_table("banking_transactions")

    # Drop cursor columns from plaid_connections
    op.drop_column("plaid_connections", "transaction_cursor_updated_at")
    op.drop_column("plaid_connections", "transaction_cursor")

    # Note: PostgreSQL doesn't support removing enum values directly
    # The enum values will remain but won't cause issues
