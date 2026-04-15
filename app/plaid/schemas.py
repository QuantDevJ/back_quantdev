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
