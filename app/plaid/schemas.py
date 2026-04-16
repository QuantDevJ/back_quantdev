from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class PlaidExchangeRequest(BaseModel):
    public_token: str = Field(min_length=1)
    institution_id: Optional[str] = Field(default=None, max_length=255)
    institution_name: Optional[str] = Field(default=None, max_length=255)


class PlaidWebhookRequest(BaseModel):
    webhook_type: str
    webhook_code: str
    item_id: str
    error: Optional[dict] = None
    new_transactions: Optional[int] = None
    removed_transactions: Optional[list[str]] = None
    initial_update_complete: Optional[bool] = None
    historical_update_complete: Optional[bool] = None


class TransactionSyncResponse(BaseModel):
    added: int
    modified: int
    removed: int
    has_more: bool
    cursor: Optional[str] = None


class BankingTransactionOut(BaseModel):
    id: str
    account_id: str
    plaid_transaction_id: str
    amount: float
    iso_currency_code: Optional[str] = None
    category: Optional[list[str]] = None
    personal_finance_category: Optional[dict] = None
    merchant_name: Optional[str] = None
    name: str
    transaction_date: date
    authorized_date: Optional[date] = None
    pending: bool
    payment_channel: Optional[str] = None
    location: Optional[dict] = None


class BankingTransactionsListResponse(BaseModel):
    transactions: list[BankingTransactionOut]
    total_count: int
    offset: int
    limit: int


class SnapshotOut(BaseModel):
    """Performance snapshot data for a single point in time."""
    id: str
    snapshot_date: date
    value: float
    cost_basis_total: Optional[float] = None
    unrealized_gain: Optional[float] = None
    unrealized_gain_pct: Optional[float] = None


class HoldingHistoryResponse(BaseModel):
    """Response containing holding history with snapshots."""
    holding_id: str
    ticker: Optional[str] = None
    security_name: str
    account_name: Optional[str] = None
    snapshots: list[SnapshotOut]
    earliest_date: Optional[date] = None
    latest_date: Optional[date] = None


class AccountSnapshotOut(BaseModel):
    """Account balance snapshot data for a single point in time."""
    id: str
    snapshot_date: date
    current_balance: float
    available_balance: Optional[float] = None
    currency: str = "USD"


class AccountHistoryResponse(BaseModel):
    """Response containing account balance history with snapshots."""
    account_id: str
    account_name: Optional[str] = None
    institution_name: Optional[str] = None
    account_type: Optional[str] = None
    snapshots: list[AccountSnapshotOut]
    earliest_date: Optional[date] = None
    latest_date: Optional[date] = None


class TimelineDataPoint(BaseModel):
    """Single data point in the aggregated timeline."""
    date: date
    total_balance: float
    account_count: int


class AccountSummary(BaseModel):
    """Summary info for an account in the aggregated response."""
    id: str
    name: Optional[str] = None
    institution_name: Optional[str] = None
    type: Optional[str] = None
    current_balance: Optional[float] = None


class AggregatedAccountHistoryResponse(BaseModel):
    """Response containing aggregated balance history across all accounts."""
    timeline: list[TimelineDataPoint]
    accounts: list[AccountSummary]
    earliest_date: Optional[date] = None
    latest_date: Optional[date] = None
    total_accounts: int


class BackfillStatusResponse(BaseModel):
    """Response containing historical backfill status for a connection."""
    status: str  # pending, in_progress, completed, failed
    progress_percent: float
    snapshots_created: int
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class BackfillTriggerResponse(BaseModel):
    """Response after triggering a backfill job."""
    message: str
    connection_id: str
    status: str
