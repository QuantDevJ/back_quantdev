"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    plaid_connection_status = postgresql.ENUM(
        "active", "disconnected", "error", "pending_refresh", name="plaid_connection_status", create_type=False
    )
    account_type = postgresql.ENUM(
        "401k", "401a", "ira", "roth_ira", "sep_ira", "403b", "hsa", "taxable", "other", name="account_type", create_type=False
    )
    security_type = postgresql.ENUM("stock", "etf", "mutual_fund", "bond", "other", name="security_type", create_type=False)
    transaction_type = postgresql.ENUM(
        "buy",
        "sell",
        "dividend",
        "interest",
        "fee",
        "split",
        "transfer_in",
        "transfer_out",
        "other",
        name="transaction_type",
        create_type=False,
    )
    chat_role = postgresql.ENUM("user", "assistant", name="chat_role", create_type=False)
    audit_status = postgresql.ENUM("success", "failure", name="audit_status", create_type=False)

    bind = op.get_bind()
    plaid_connection_status.create(bind, checkfirst=True)
    account_type.create(bind, checkfirst=True)
    security_type.create(bind, checkfirst=True)
    transaction_type.create(bind, checkfirst=True)
    chat_role.create(bind, checkfirst=True)
    audit_status.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email_hash", sa.String(length=255), nullable=False),
        sa.Column("email_encrypted", postgresql.BYTEA(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("settings_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_hash"),
    )

    op.create_table(
        "plaid_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plaid_item_id", sa.String(length=255), nullable=False),
        sa.Column("access_token_encrypted", postgresql.BYTEA(), nullable=False),
        sa.Column("institution_name", sa.String(length=255), nullable=True),
        sa.Column("institution_id", sa.String(length=255), nullable=True),
        sa.Column("status", plaid_connection_status, server_default="active", nullable=True),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("sync_frequency_hours", sa.Integer(), server_default=sa.text("24"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "plaid_item_id"),
    )

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plaid_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plaid_account_id", sa.String(length=255), nullable=False),
        sa.Column("account_type", account_type, nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("account_subtype", sa.String(length=255), nullable=True),
        sa.Column("institution_name", sa.String(length=255), nullable=True),
        sa.Column("current_balance", sa.Numeric(15, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=True),
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_balance_update", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["plaid_connection_id"], ["plaid_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "plaid_account_id"),
    )

    op.create_table(
        "securities",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("security_type", security_type, nullable=False),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("geography", sa.String(length=100), nullable=True),
        sa.Column("expense_ratio", sa.Numeric(6, 4), nullable=True),
        sa.Column("fund_family", sa.String(length=100), nullable=True),
        sa.Column("cusip", sa.String(length=9), nullable=True),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("is_index", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker"),
    )

    op.create_table(
        "holdings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plaid_holding_id", sa.String(length=255), nullable=True),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("cost_basis_per_share", sa.Numeric(15, 6), nullable=True),
        sa.Column("cost_basis_total", sa.Numeric(15, 2), nullable=True),
        sa.Column("current_price", sa.Numeric(15, 6), nullable=True),
        sa.Column("current_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "fund_holdings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("parent_security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("underlying_security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weight", sa.Numeric(6, 4), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["parent_security_id"], ["securities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["underlying_security_id"], ["securities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_security_id", "underlying_security_id", "as_of_date"),
    )

    op.create_table(
        "tax_lots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("holding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("quantity_purchased", sa.Numeric(18, 8), nullable=False),
        sa.Column("cost_basis_per_share", sa.Numeric(15, 6), nullable=False),
        sa.Column("cost_basis_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("quantity_remaining", sa.Numeric(18, 8), nullable=False),
        sa.Column("quantity_sold", sa.Numeric(18, 8), server_default=sa.text("0"), nullable=True),
        sa.Column("is_long_term", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("gain_loss_per_share", sa.Numeric(15, 6), nullable=True),
        sa.Column("gain_loss_total", sa.Numeric(15, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["holding_id"], ["holdings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_type", transaction_type, nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("price_per_unit", sa.Numeric(15, 6), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("tax_lot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("plaid_transaction_id", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tax_lot_id"], ["tax_lots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plaid_transaction_id"),
    )

    op.create_table(
        "allocation_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_class", sa.String(length=100), nullable=False),
        sa.Column("target_percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("drift_threshold_percentage", sa.Numeric(5, 2), server_default=sa.text("5.00"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "asset_class"),
    )

    op.create_table(
        "performance_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("holding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(15, 2), nullable=False),
        sa.Column("cost_basis_total", sa.Numeric(15, 2), nullable=True),
        sa.Column("unrealized_gain", sa.Numeric(15, 2), nullable=True),
        sa.Column("unrealized_gain_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["holding_id"], ["holdings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("holding_id", "snapshot_date"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", chat_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_user_conversation_created", "chat_messages", ["user_id", "conversation_id", "created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", audit_status, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_user_created", "audit_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_user_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_chat_messages_user_conversation_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("performance_snapshots")
    op.drop_table("allocation_targets")
    op.drop_table("transactions")
    op.drop_table("tax_lots")
    op.drop_table("fund_holdings")
    op.drop_table("holdings")
    op.drop_table("securities")
    op.drop_table("accounts")
    op.drop_table("plaid_connections")
    op.drop_table("users")

    bind = op.get_bind()
    sa.Enum(name="audit_status").drop(bind, checkfirst=True)
    sa.Enum(name="chat_role").drop(bind, checkfirst=True)
    sa.Enum(name="transaction_type").drop(bind, checkfirst=True)
    sa.Enum(name="security_type").drop(bind, checkfirst=True)
    sa.Enum(name="account_type").drop(bind, checkfirst=True)
    sa.Enum(name="plaid_connection_status").drop(bind, checkfirst=True)
