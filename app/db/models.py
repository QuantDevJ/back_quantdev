import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import BYTEA, INET, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PlaidConnectionStatus(str, enum.Enum):
    active = "active"
    disconnected = "disconnected"
    error = "error"
    pending_refresh = "pending_refresh"


class SyncStatus(str, enum.Enum):
    pending = "pending"
    syncing = "syncing"
    completed = "completed"
    failed = "failed"


class AccountType(str, enum.Enum):
    k401 = "401k"
    a401 = "401a"
    ira = "ira"
    roth_ira = "roth_ira"
    sep_ira = "sep_ira"
    b403 = "403b"
    hsa = "hsa"
    taxable = "taxable"
    checking = "checking"
    savings = "savings"
    money_market = "money_market"
    cd = "cd"
    credit_card = "credit_card"
    other = "other"


class SecurityType(str, enum.Enum):
    stock = "stock"
    etf = "etf"
    mutual_fund = "mutual_fund"
    bond = "bond"
    other = "other"


class TransactionType(str, enum.Enum):
    buy = "buy"
    sell = "sell"
    dividend = "dividend"
    interest = "interest"
    fee = "fee"
    split = "split"
    transfer_in = "transfer_in"
    transfer_out = "transfer_out"
    other = "other"


class ChatRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class AuditStatus(str, enum.Enum):
    success = "success"
    failure = "failure"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    email_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email_encrypted: Mapped[Optional[bytes]] = mapped_column(BYTEA, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    settings_json: Mapped[dict] = mapped_column(JSON, server_default="{}", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_token_hash", "token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    request_ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PlaidConnection(Base):
    __tablename__ = "plaid_connections"
    __table_args__ = (UniqueConstraint("user_id", "plaid_item_id", name="uq_plaid_connections_user_item"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plaid_item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token_encrypted: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    institution_name: Mapped[Optional[str]] = mapped_column(String(255))
    institution_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[PlaidConnectionStatus] = mapped_column(
        Enum(PlaidConnectionStatus, name="plaid_connection_status"),
        server_default=PlaidConnectionStatus.active.value,
        nullable=False,
    )
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text)
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status"),
        server_default=SyncStatus.pending.value,
        nullable=False,
    )
    sync_frequency_hours: Mapped[int] = mapped_column(server_default="24", nullable=False)
    transaction_cursor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transaction_cursor_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("user_id", "plaid_account_id", name="uq_accounts_user_plaid_account"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plaid_connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plaid_connections.id", ondelete="CASCADE"), nullable=False)
    plaid_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    account_name: Mapped[Optional[str]] = mapped_column(String(255))
    official_name: Mapped[Optional[str]] = mapped_column(String(500))
    account_subtype: Mapped[Optional[str]] = mapped_column(String(255))
    mask: Mapped[Optional[str]] = mapped_column(String(10))
    institution_name: Mapped[Optional[str]] = mapped_column(String(255))
    current_balance: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    available_balance: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), server_default="USD", nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    last_balance_update: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Security(Base):
    __tablename__ = "securities"
    __table_args__ = (
        Index("ix_securities_plaid_security_id", "plaid_security_id", unique=True, postgresql_where="plaid_security_id IS NOT NULL"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    ticker: Mapped[Optional[str]] = mapped_column(String(50), unique=True, nullable=True)
    plaid_security_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    security_type: Mapped[SecurityType] = mapped_column(Enum(SecurityType, name="security_type"), nullable=False)
    sector: Mapped[Optional[str]] = mapped_column(String(100))
    geography: Mapped[Optional[str]] = mapped_column(String(100))
    expense_ratio: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    fund_family: Mapped[Optional[str]] = mapped_column(String(100))
    cusip: Mapped[Optional[str]] = mapped_column(String(9))
    isin: Mapped[Optional[str]] = mapped_column(String(12))
    is_index: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    close_price: Mapped[Optional[float]] = mapped_column(Numeric(15, 6))
    close_price_as_of: Mapped[Optional[date]] = mapped_column(Date)
    iso_currency_code: Mapped[Optional[str]] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("account_id", "security_id", name="uq_holdings_account_security"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    plaid_security_id: Mapped[Optional[str]] = mapped_column(String(255))
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    cost_basis_per_share: Mapped[Optional[float]] = mapped_column(Numeric(15, 6))
    cost_basis_total: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    current_price: Mapped[Optional[float]] = mapped_column(Numeric(15, 6))
    current_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    iso_currency_code: Mapped[Optional[str]] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class FundHolding(Base):
    __tablename__ = "fund_holdings"
    __table_args__ = (
        UniqueConstraint("parent_security_id", "underlying_security_id", "as_of_date", name="uq_fund_holdings"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    parent_security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    underlying_security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    weight: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TaxLot(Base):
    __tablename__ = "tax_lots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    holding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_purchased: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    cost_basis_per_share: Mapped[float] = mapped_column(Numeric(15, 6), nullable=False)
    cost_basis_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    quantity_remaining: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    quantity_sold: Mapped[float] = mapped_column(Numeric(18, 8), server_default="0", nullable=False)
    is_long_term: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    gain_loss_per_share: Mapped[Optional[float]] = mapped_column(Numeric(15, 6))
    gain_loss_total: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    transaction_type: Mapped[TransactionType] = mapped_column(Enum(TransactionType, name="transaction_type"), nullable=False)
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(18, 8))
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    price_per_unit: Mapped[Optional[float]] = mapped_column(Numeric(15, 6))
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    settlement_date: Mapped[Optional[date]] = mapped_column(Date)
    tax_lot_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tax_lots.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    plaid_transaction_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)


class AllocationTarget(Base):
    __tablename__ = "allocation_targets"
    __table_args__ = (UniqueConstraint("user_id", "asset_class", name="uq_allocation_target_user_asset"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(100), nullable=False)
    target_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    drift_threshold_percentage: Mapped[float] = mapped_column(Numeric(5, 2), server_default="5.00", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshots"
    __table_args__ = (UniqueConstraint("holding_id", "snapshot_date", name="uq_perf_snapshot_holding_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    holding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    cost_basis_total: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    unrealized_gain: Mapped[Optional[float]] = mapped_column(Numeric(15, 2))
    unrealized_gain_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    role: Mapped[ChatRole] = mapped_column(Enum(ChatRole, name="chat_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[Optional[dict]] = mapped_column(JSON)
    sources: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_chat_messages_user_conversation_created", "user_id", "conversation_id", "created_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100))
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    status: Mapped[AuditStatus] = mapped_column(Enum(AuditStatus, name="audit_status"), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BankingTransaction(Base):
    __tablename__ = "banking_transactions"
    __table_args__ = (
        Index("ix_banking_transactions_account_date", "account_id", "transaction_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    plaid_transaction_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    iso_currency_code: Mapped[Optional[str]] = mapped_column(String(3))
    category: Mapped[Optional[list]] = mapped_column(JSON)
    personal_finance_category: Mapped[Optional[dict]] = mapped_column(JSON)
    merchant_name: Mapped[Optional[str]] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    authorized_date: Mapped[Optional[date]] = mapped_column(Date)
    pending: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    payment_channel: Mapped[Optional[str]] = mapped_column(String(50))
    location_json: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
