"""Add Plaid investment tracking columns

Revision ID: 0003_plaid_investment_columns
Revises: 0002_password_reset_tokens
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_plaid_investment_columns"
down_revision = "0002_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Security table updates
    op.add_column("securities", sa.Column("plaid_security_id", sa.String(length=255), nullable=True))
    op.add_column("securities", sa.Column("close_price", sa.Numeric(precision=15, scale=6), nullable=True))
    op.add_column("securities", sa.Column("close_price_as_of", sa.Date(), nullable=True))
    op.add_column("securities", sa.Column("iso_currency_code", sa.String(length=3), nullable=True))

    # Make ticker nullable (some Plaid securities don't have tickers)
    op.alter_column("securities", "ticker", existing_type=sa.String(length=10), type_=sa.String(length=50), nullable=True)

    # Create partial unique index on plaid_security_id
    op.create_index(
        "ix_securities_plaid_security_id",
        "securities",
        ["plaid_security_id"],
        unique=True,
        postgresql_where=sa.text("plaid_security_id IS NOT NULL"),
    )

    # Account table updates
    op.add_column("accounts", sa.Column("official_name", sa.String(length=500), nullable=True))
    op.add_column("accounts", sa.Column("mask", sa.String(length=10), nullable=True))
    op.add_column("accounts", sa.Column("available_balance", sa.Numeric(precision=15, scale=2), nullable=True))
    op.add_column("accounts", sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True))

    # Holding table updates
    op.add_column("holdings", sa.Column("plaid_security_id", sa.String(length=255), nullable=True))
    op.add_column("holdings", sa.Column("iso_currency_code", sa.String(length=3), nullable=True))

    # Drop old plaid_holding_id column if it exists (not used by Plaid API)
    op.drop_column("holdings", "plaid_holding_id")

    # Create unique constraint on holdings for account_id + security_id
    op.create_unique_constraint("uq_holdings_account_security", "holdings", ["account_id", "security_id"])


def downgrade() -> None:
    # Holdings
    op.drop_constraint("uq_holdings_account_security", "holdings", type_="unique")
    op.add_column("holdings", sa.Column("plaid_holding_id", sa.String(length=255), nullable=True))
    op.drop_column("holdings", "iso_currency_code")
    op.drop_column("holdings", "plaid_security_id")

    # Accounts
    op.drop_column("accounts", "updated_at")
    op.drop_column("accounts", "available_balance")
    op.drop_column("accounts", "mask")
    op.drop_column("accounts", "official_name")

    # Securities
    op.drop_index("ix_securities_plaid_security_id", table_name="securities")
    op.alter_column("securities", "ticker", existing_type=sa.String(length=50), type_=sa.String(length=10), nullable=False)
    op.drop_column("securities", "iso_currency_code")
    op.drop_column("securities", "close_price_as_of")
    op.drop_column("securities", "close_price")
    op.drop_column("securities", "plaid_security_id")
