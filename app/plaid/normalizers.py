"""Plaid data normalization layer for clean data transformation.

This module provides dataclasses and a normalizer class to transform
Plaid API responses into clean, typed objects for database storage.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.db.models import AccountType, SecurityType, TransactionType


@dataclass
class NormalizedSecurity:
    """Normalized security data from Plaid."""
    plaid_security_id: str
    ticker: Optional[str]
    name: str
    security_type: SecurityType
    close_price: Optional[float]
    close_price_as_of: Optional[date]
    iso_currency_code: Optional[str]
    cusip: Optional[str] = None
    isin: Optional[str] = None


@dataclass
class NormalizedHolding:
    """Normalized holding data from Plaid."""
    plaid_account_id: str
    plaid_security_id: str
    quantity: float
    cost_basis_total: Optional[float]
    cost_basis_per_share: Optional[float]
    institution_price: Optional[float]
    institution_value: Optional[float]
    as_of_date: date
    iso_currency_code: Optional[str]


@dataclass
class NormalizedAccount:
    """Normalized account data from Plaid."""
    plaid_account_id: str
    account_type: AccountType
    account_name: Optional[str]
    official_name: Optional[str]
    account_subtype: Optional[str]
    mask: Optional[str]
    current_balance: Optional[float]
    available_balance: Optional[float]
    currency: str


@dataclass
class NormalizedTransaction:
    """Normalized investment transaction data from Plaid."""
    plaid_transaction_id: str
    plaid_account_id: str
    plaid_security_id: Optional[str]
    transaction_type: TransactionType
    quantity: Optional[float]
    amount: float
    price_per_unit: Optional[float]
    transaction_date: date
    fees: Optional[float] = None


class PlaidInvestmentsNormalizer:
    """Normalizes Plaid investments API data for database storage.

    This class provides methods to transform raw Plaid API responses
    into clean, typed dataclasses that can be used for database upserts.
    """

    def normalize_security(self, plaid_security) -> NormalizedSecurity:
        """Normalize a Plaid security object.

        Args:
            plaid_security: Raw security object from Plaid API

        Returns:
            NormalizedSecurity with mapped types and extracted values
        """
        security_type = self._map_security_type(plaid_security.type)

        close_price = None
        if plaid_security.close_price is not None:
            close_price = float(plaid_security.close_price)

        close_price_as_of = None
        if plaid_security.close_price_as_of:
            close_price_as_of = plaid_security.close_price_as_of

        return NormalizedSecurity(
            plaid_security_id=plaid_security.security_id,
            ticker=plaid_security.ticker_symbol,
            name=plaid_security.name or "Unknown Security",
            security_type=security_type,
            close_price=close_price,
            close_price_as_of=close_price_as_of,
            iso_currency_code=plaid_security.iso_currency_code,
            cusip=getattr(plaid_security, "cusip", None),
            isin=getattr(plaid_security, "isin", None),
        )

    def normalize_holding(self, plaid_holding) -> NormalizedHolding:
        """Normalize a Plaid holding object.

        Args:
            plaid_holding: Raw holding object from Plaid API

        Returns:
            NormalizedHolding with calculated cost basis and proper as_of_date
        """
        quantity = float(plaid_holding.quantity) if plaid_holding.quantity is not None else 0.0

        cost_basis_total = None
        if plaid_holding.cost_basis is not None:
            cost_basis_total = float(plaid_holding.cost_basis)

        cost_basis_per_share = None
        if cost_basis_total is not None and quantity != 0:
            cost_basis_per_share = cost_basis_total / quantity

        institution_price = None
        if plaid_holding.institution_price is not None:
            institution_price = float(plaid_holding.institution_price)

        institution_value = None
        if plaid_holding.institution_value is not None:
            institution_value = float(plaid_holding.institution_value)

        # Extract as_of_date from institution_price_as_of, defaulting to today
        as_of_date = date.today()
        if hasattr(plaid_holding, "institution_price_as_of") and plaid_holding.institution_price_as_of:
            as_of_date = plaid_holding.institution_price_as_of

        return NormalizedHolding(
            plaid_account_id=plaid_holding.account_id,
            plaid_security_id=plaid_holding.security_id,
            quantity=quantity,
            cost_basis_total=cost_basis_total,
            cost_basis_per_share=cost_basis_per_share,
            institution_price=institution_price,
            institution_value=institution_value,
            as_of_date=as_of_date,
            iso_currency_code=plaid_holding.iso_currency_code,
        )

    def normalize_account(self, plaid_account) -> Optional[NormalizedAccount]:
        """Normalize a Plaid account object.

        Args:
            plaid_account: Raw account object from Plaid API

        Returns:
            NormalizedAccount if it's an investment account, None otherwise
        """
        acc_type_val = plaid_account.type.value if hasattr(plaid_account.type, "value") else plaid_account.type
        acc_subtype_val = plaid_account.subtype.value if hasattr(plaid_account.subtype, "value") else plaid_account.subtype

        # Only process investment accounts
        if acc_type_val != "investment":
            return None

        account_type = self._map_account_type(acc_type_val, acc_subtype_val)

        current_balance = None
        if plaid_account.balances.current is not None:
            current_balance = float(plaid_account.balances.current)

        available_balance = None
        if plaid_account.balances.available is not None:
            available_balance = float(plaid_account.balances.available)

        return NormalizedAccount(
            plaid_account_id=plaid_account.account_id,
            account_type=account_type,
            account_name=plaid_account.name,
            official_name=getattr(plaid_account, "official_name", None),
            account_subtype=acc_subtype_val,
            mask=plaid_account.mask,
            current_balance=current_balance,
            available_balance=available_balance,
            currency=plaid_account.balances.iso_currency_code or "USD",
        )

    def normalize_transaction(self, plaid_txn) -> NormalizedTransaction:
        """Normalize a Plaid investment transaction object.

        Args:
            plaid_txn: Raw investment transaction object from Plaid API

        Returns:
            NormalizedTransaction with mapped transaction type
        """
        transaction_type = self._map_transaction_type(plaid_txn.type, plaid_txn.subtype)

        quantity = None
        if plaid_txn.quantity is not None:
            quantity = float(plaid_txn.quantity)

        amount = float(plaid_txn.amount) if plaid_txn.amount is not None else 0.0

        price_per_unit = None
        if plaid_txn.price is not None:
            price_per_unit = float(plaid_txn.price)

        fees = None
        if hasattr(plaid_txn, "fees") and plaid_txn.fees is not None:
            fees = float(plaid_txn.fees)

        return NormalizedTransaction(
            plaid_transaction_id=plaid_txn.investment_transaction_id,
            plaid_account_id=plaid_txn.account_id,
            plaid_security_id=plaid_txn.security_id,
            transaction_type=transaction_type,
            quantity=quantity,
            amount=amount,
            price_per_unit=price_per_unit,
            transaction_date=plaid_txn.date,
            fees=fees,
        )

    def _map_security_type(self, plaid_type: Optional[str]) -> SecurityType:
        """Map Plaid security type to SecurityType enum.

        Args:
            plaid_type: Raw type string from Plaid API

        Returns:
            Mapped SecurityType enum value
        """
        type_lower = (plaid_type or "").lower()
        if type_lower == "equity":
            return SecurityType.stock
        if type_lower == "etf":
            return SecurityType.etf
        if type_lower in ("mutual fund", "mutual_fund"):
            return SecurityType.mutual_fund
        if type_lower in ("fixed income", "fixed_income", "bond"):
            return SecurityType.bond
        return SecurityType.other

    def _map_account_type(self, plaid_type: Optional[str], plaid_subtype: Optional[str]) -> AccountType:
        """Map Plaid account type/subtype to AccountType enum.

        Args:
            plaid_type: Raw type string from Plaid API
            plaid_subtype: Raw subtype string from Plaid API

        Returns:
            Mapped AccountType enum value
        """
        subtype = (plaid_subtype or "").lower().replace(" ", "_")
        if subtype in ("401k", "401a"):
            return AccountType.k401
        if subtype == "403b":
            return AccountType.b403
        if subtype == "ira":
            return AccountType.ira
        if subtype in ("roth", "roth_ira"):
            return AccountType.roth_ira
        if subtype == "sep_ira":
            return AccountType.sep_ira
        if subtype == "hsa":
            return AccountType.hsa
        if plaid_type == "investment":
            return AccountType.taxable
        return AccountType.other

    def _map_transaction_type(self, plaid_type, plaid_subtype) -> TransactionType:
        """Map Plaid investment transaction type to TransactionType enum.

        Args:
            plaid_type: Raw type from Plaid API (may be enum or string)
            plaid_subtype: Raw subtype from Plaid API (may be enum or string)

        Returns:
            Mapped TransactionType enum value
        """
        type_val = plaid_type.value if hasattr(plaid_type, "value") else plaid_type
        subtype_val = plaid_subtype.value if hasattr(plaid_subtype, "value") else plaid_subtype
        type_lower = (type_val or "").lower()
        subtype_lower = (subtype_val or "").lower()

        if type_lower == "buy" or subtype_lower == "buy":
            return TransactionType.buy
        if type_lower == "sell" or subtype_lower == "sell":
            return TransactionType.sell
        if type_lower == "dividend" or subtype_lower in ("dividend", "qualified dividend", "non-qualified dividend"):
            return TransactionType.dividend
        if type_lower == "interest" or subtype_lower == "interest":
            return TransactionType.interest
        if type_lower == "fee" or subtype_lower in ("fee", "management fee", "account fee"):
            return TransactionType.fee
        if subtype_lower in ("split", "stock split"):
            return TransactionType.split
        if type_lower == "transfer" or subtype_lower == "transfer":
            if "in" in subtype_lower:
                return TransactionType.transfer_in
            if "out" in subtype_lower:
                return TransactionType.transfer_out
            return TransactionType.transfer_in
        return TransactionType.other
