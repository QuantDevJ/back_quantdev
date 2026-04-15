import json
import logging
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from plaid.exceptions import ApiException as PlaidApiException
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from sqlalchemy.orm import Session
from starlette import status

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.security import decrypt_email, encrypt_email
from app.db.models import Account, AccountType, BankingTransaction, Holding, PlaidConnection, PlaidConnectionStatus, Security, SecurityType, SyncStatus, Transaction, TransactionType
from app.plaid.normalizers import PlaidInvestmentsNormalizer
from app.plaid.snapshots import SnapshotService


def _plaid_error_message(exc: PlaidApiException) -> str:
    raw = exc.body
    if raw is None:
        return str(exc.reason or exc)
    if isinstance(raw, (bytes, bytearray)):
        try:
            data = json.loads(raw.decode())
            if isinstance(data, dict):
                return str(
                    data.get("error_message")
                    or data.get("display_message")
                    or data.get("error_code")
                    or data
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return raw.decode(errors="replace")[:500]
    if isinstance(raw, dict):
        return str(raw.get("error_message") or raw.get("error_code") or raw)
    return str(raw)[:500]


class PlaidService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_link_token(self, *, user_id: UUID) -> dict:
        from app.plaid.client import get_plaid_client

        client = get_plaid_client()
        try:
            request_kwargs = {
                "products": [Products("transactions"), Products("investments")],
                "client_name": "Quant.ly",
                "language": "en",
                "country_codes": [CountryCode("US")],
                "user": LinkTokenCreateRequestUser(client_user_id=str(user_id)),
            }
            if settings.backend_base_url:
                request_kwargs["webhook"] = f"{settings.backend_base_url.rstrip('/')}/v1/plaid/webhook"
            request = LinkTokenCreateRequest(**request_kwargs)
            response = client.link_token_create(request)
        except PlaidApiException as e:
            raise AppException(
                code="PLAID_ERROR",
                message=_plaid_error_message(e),
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from e

        return {"link_token": response.link_token}

    def exchange_public_token(
        self,
        *,
        user_id: UUID,
        public_token: str,
        institution_id: str | None,
        institution_name: str | None,
    ) -> dict:
        from app.plaid.client import get_plaid_client

        client = get_plaid_client()
        try:
            req = ItemPublicTokenExchangeRequest(public_token=public_token)
            response = client.item_public_token_exchange(req)
        except PlaidApiException as e:
            raise AppException(
                code="PLAID_ERROR",
                message=_plaid_error_message(e),
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from e

        access_token = response.access_token
        item_id = response.item_id

        encrypted = encrypt_email(access_token)
        if encrypted is None:
            raise AppException(
                code="CONFIG_ERROR",
                message=(
                    "No encryption key configured for storing Plaid tokens. "
                    "Set EMAIL_ENCRYPTION_KEY or DATA_ENCRYPTION_KEY (base64, 32 bytes), "
                    "or run with APP_DEBUG=true for local development (key derived from JWT_SECRET_KEY)."
                ),
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        existing = (
            self.db.query(PlaidConnection)
            .filter(
                PlaidConnection.user_id == user_id,
                PlaidConnection.plaid_item_id == item_id,
            )
            .first()
        )
        if existing:
            raise AppException(
                code="VALIDATION_ERROR",
                message="This bank connection is already linked.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        row = PlaidConnection(
            user_id=user_id,
            plaid_item_id=item_id,
            access_token_encrypted=encrypted,
            institution_id=institution_id,
            institution_name=institution_name,
            status=PlaidConnectionStatus.active,
            sync_status=SyncStatus.pending,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        # Store ID before potentially failing operation
        connection_id = row.id

        # Set sync status to syncing before starting
        row.sync_status = SyncStatus.syncing
        self.db.commit()

        # Ingest investment data and sync banking transactions immediately after connection
        try:
            # Sync investment holdings and transactions
            self.ingest_investments(user_id=user_id, connection_id=connection_id)

            # Also sync banking transactions to populate transaction_cursor
            try:
                self.sync_transactions(user_id=user_id, connection_id=connection_id)
            except Exception as txn_err:
                # Log but don't fail - some accounts may not have banking transactions
                logger.warning("Banking transaction sync skipped for connection %s: %s", connection_id, txn_err)

            row.sync_status = SyncStatus.completed
            self.db.commit()
        except Exception as e:
            logger.exception("Initial sync failed for connection %s", connection_id)
            self.db.rollback()
            # Re-fetch the row after rollback to update it
            row = self.db.query(PlaidConnection).filter(PlaidConnection.id == connection_id).first()
            if row:
                row.sync_status = SyncStatus.failed
                row.last_sync_error = str(e)[:500]
                self.db.commit()

        return {
            "plaid_connection_id": str(connection_id),
            "item_id": item_id,
            "institution_name": institution_name,
        }

    def list_connections(self, *, user_id: UUID) -> list[dict]:
        rows = (
            self.db.query(PlaidConnection)
            .filter(PlaidConnection.user_id == user_id)
            .order_by(PlaidConnection.created_at.desc())
            .all()
        )
        return [
            {
                "id": str(r.id),
                "institution_name": r.institution_name,
                "institution_id": r.institution_id,
                "status": r.status.value,
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            }
            for r in rows
        ]

    def disconnect_connection(self, *, user_id: UUID, connection_id: UUID) -> dict:
        from plaid.model.item_remove_request import ItemRemoveRequest

        row = (
            self.db.query(PlaidConnection)
            .filter(
                PlaidConnection.id == connection_id,
                PlaidConnection.user_id == user_id,
            )
            .first()
        )
        if not row:
            raise AppException(
                code="NOT_FOUND",
                message="Plaid connection not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        access_token = decrypt_email(row.access_token_encrypted)
        if not access_token:
            raise AppException(
                code="CONFIG_ERROR",
                message="Could not decrypt stored Plaid token.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        from app.plaid.client import get_plaid_client

        client = get_plaid_client()
        try:
            client.item_remove(ItemRemoveRequest(access_token=access_token))
        except PlaidApiException as e:
            err_code: str | None = None
            if isinstance(e.body, (bytes, bytearray)):
                try:
                    parsed = json.loads(e.body.decode())
                    if isinstance(parsed, dict):
                        err_code = str(parsed.get("error_code") or "")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            if err_code not in ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"):
                raise AppException(
                    code="PLAID_ERROR",
                    message=_plaid_error_message(e),
                    status_code=status.HTTP_502_BAD_GATEWAY,
                ) from e

        self.db.delete(row)
        self.db.commit()
        return {"disconnected": True}

    def get_investments(self, *, user_id: UUID, connection_id: UUID) -> dict:
        from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest

        row = (
            self.db.query(PlaidConnection)
            .filter(
                PlaidConnection.id == connection_id,
                PlaidConnection.user_id == user_id,
            )
            .first()
        )
        if not row:
            raise AppException(
                code="NOT_FOUND",
                message="Plaid connection not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if row.status != PlaidConnectionStatus.active:
            raise AppException(
                code="VALIDATION_ERROR",
                message="Plaid connection is not active.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        access_token = decrypt_email(row.access_token_encrypted)
        if not access_token:
            raise AppException(
                code="CONFIG_ERROR",
                message="Could not decrypt stored Plaid token.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        from app.plaid.client import get_plaid_client

        client = get_plaid_client()
        try:
            request = InvestmentsHoldingsGetRequest(access_token=access_token)
            response = client.investments_holdings_get(request)
        except PlaidApiException as e:
            err_code: str | None = None
            if isinstance(e.body, (bytes, bytearray)):
                try:
                    parsed = json.loads(e.body.decode())
                    if isinstance(parsed, dict):
                        err_code = str(parsed.get("error_code") or "")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            if err_code == "ITEM_LOGIN_REQUIRED":
                raise AppException(
                    code="PLAID_REAUTH_REQUIRED",
                    message="Bank login expired. Please reconnect your account.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                ) from e
            if err_code == "INVALID_ACCESS_TOKEN":
                raise AppException(
                    code="PLAID_INVALID_TOKEN",
                    message="Invalid access token. Please reconnect your account.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                ) from e

            raise AppException(
                code="PLAID_ERROR",
                message=_plaid_error_message(e),
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from e

        accounts = []
        for acc in response.accounts:
            acc_type = acc.type
            acc_subtype = acc.subtype
            accounts.append({
                "account_id": acc.account_id,
                "name": acc.name,
                "type": acc_type.value if hasattr(acc_type, "value") else acc_type,
                "subtype": acc_subtype.value if hasattr(acc_subtype, "value") else acc_subtype,
                "mask": acc.mask,
                "balances": {
                    "current": float(acc.balances.current) if acc.balances.current is not None else None,
                    "available": float(acc.balances.available) if acc.balances.available is not None else None,
                    "iso_currency_code": acc.balances.iso_currency_code,
                },
            })

        holdings = []
        for h in response.holdings:
            holdings.append({
                "account_id": h.account_id,
                "security_id": h.security_id,
                "quantity": float(h.quantity) if h.quantity is not None else None,
                "institution_price": float(h.institution_price) if h.institution_price is not None else None,
                "institution_value": float(h.institution_value) if h.institution_value is not None else None,
                "cost_basis": float(h.cost_basis) if h.cost_basis is not None else None,
                "iso_currency_code": h.iso_currency_code,
            })

        securities = []
        for s in response.securities:
            close_price_as_of = s.close_price_as_of
            securities.append({
                "security_id": s.security_id,
                "name": s.name,
                "ticker_symbol": s.ticker_symbol,
                "type": s.type,
                "close_price": float(s.close_price) if s.close_price is not None else None,
                "close_price_as_of": close_price_as_of.isoformat() if close_price_as_of else None,
                "iso_currency_code": s.iso_currency_code,
            })

        return {
            "accounts": accounts,
            "holdings": holdings,
            "securities": securities,
        }

    def _map_plaid_account_type(self, plaid_type: str | None, plaid_subtype: str | None) -> AccountType:
        """Map Plaid account type/subtype to our AccountType enum."""
        subtype = (plaid_subtype or "").lower().replace(" ", "_")
        if subtype in ("401k", "401a"):
            return AccountType.k401
        if subtype == "403b":
            return AccountType.b403
        if subtype == "ira":
            return AccountType.ira
        if subtype == "roth" or subtype == "roth_ira":
            return AccountType.roth_ira
        if subtype == "sep_ira":
            return AccountType.sep_ira
        if subtype == "hsa":
            return AccountType.hsa
        if plaid_type == "investment":
            return AccountType.taxable
        return AccountType.other

    def _map_plaid_security_type(self, plaid_type: str | None) -> SecurityType:
        """Map Plaid security type to our SecurityType enum."""
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

    def _map_plaid_transaction_type(self, plaid_type, plaid_subtype) -> TransactionType:
        """Map Plaid investment transaction type to our TransactionType enum."""
        # Extract value from Plaid enum objects if needed
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
            return TransactionType.transfer_in  # Default to transfer_in
        return TransactionType.other

    def ingest_investments(self, *, user_id: UUID, connection_id: UUID) -> dict:
        """Fetch investments from Plaid and store in database with upsert logic."""
        from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest

        row = (
            self.db.query(PlaidConnection)
            .filter(
                PlaidConnection.id == connection_id,
                PlaidConnection.user_id == user_id,
            )
            .first()
        )
        if not row:
            raise AppException(
                code="NOT_FOUND",
                message="Plaid connection not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if row.status != PlaidConnectionStatus.active:
            raise AppException(
                code="VALIDATION_ERROR",
                message="Plaid connection is not active.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        access_token = decrypt_email(row.access_token_encrypted)
        if not access_token:
            raise AppException(
                code="CONFIG_ERROR",
                message="Could not decrypt stored Plaid token.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        from app.plaid.client import get_plaid_client

        client = get_plaid_client()
        try:
            request = InvestmentsHoldingsGetRequest(access_token=access_token)
            response = client.investments_holdings_get(request)
        except PlaidApiException as e:
            err_code: str | None = None
            if isinstance(e.body, (bytes, bytearray)):
                try:
                    parsed = json.loads(e.body.decode())
                    if isinstance(parsed, dict):
                        err_code = str(parsed.get("error_code") or "")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            if err_code == "ITEM_LOGIN_REQUIRED":
                raise AppException(
                    code="PLAID_REAUTH_REQUIRED",
                    message="Bank login expired. Please reconnect your account.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                ) from e

            raise AppException(
                code="PLAID_ERROR",
                message=_plaid_error_message(e),
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from e

        # Initialize normalizer for data transformation
        normalizer = PlaidInvestmentsNormalizer()

        # Step 1: Upsert securities using normalizer
        plaid_security_map: dict[str, UUID] = {}  # plaid_security_id -> our security.id
        for s in response.securities:
            normalized = normalizer.normalize_security(s)
            plaid_sec_id = normalized.plaid_security_id

            # Try to find existing security by plaid_security_id first
            existing = (
                self.db.query(Security)
                .filter(Security.plaid_security_id == plaid_sec_id)
                .first()
            )

            if existing:
                # Update existing security
                existing.name = normalized.name
                existing.security_type = normalized.security_type
                if normalized.close_price is not None:
                    existing.close_price = normalized.close_price
                if normalized.close_price_as_of:
                    existing.close_price_as_of = normalized.close_price_as_of
                existing.iso_currency_code = normalized.iso_currency_code
                plaid_security_map[plaid_sec_id] = existing.id
            else:
                # Try to find by ticker if it exists
                if normalized.ticker:
                    existing_by_ticker = (
                        self.db.query(Security)
                        .filter(Security.ticker == normalized.ticker)
                        .first()
                    )
                    if existing_by_ticker:
                        # Update with plaid_security_id
                        existing_by_ticker.plaid_security_id = plaid_sec_id
                        existing_by_ticker.name = normalized.name
                        existing_by_ticker.security_type = normalized.security_type
                        if normalized.close_price is not None:
                            existing_by_ticker.close_price = normalized.close_price
                        if normalized.close_price_as_of:
                            existing_by_ticker.close_price_as_of = normalized.close_price_as_of
                        existing_by_ticker.iso_currency_code = normalized.iso_currency_code
                        plaid_security_map[plaid_sec_id] = existing_by_ticker.id
                        continue

                # Create new security
                new_sec = Security(
                    ticker=normalized.ticker,
                    plaid_security_id=plaid_sec_id,
                    name=normalized.name,
                    security_type=normalized.security_type,
                    close_price=normalized.close_price,
                    close_price_as_of=normalized.close_price_as_of,
                    iso_currency_code=normalized.iso_currency_code,
                    cusip=normalized.cusip,
                    isin=normalized.isin,
                )
                self.db.add(new_sec)
                self.db.flush()  # Get the ID
                plaid_security_map[plaid_sec_id] = new_sec.id

        # Step 2: Upsert accounts using normalizer (only investment type accounts)
        plaid_account_map: dict[str, UUID] = {}  # plaid_account_id -> our account.id
        for acc in response.accounts:
            normalized = normalizer.normalize_account(acc)

            # Skip non-investment accounts (normalizer returns None)
            if normalized is None:
                continue

            plaid_acc_id = normalized.plaid_account_id

            # Try to find existing account
            existing = (
                self.db.query(Account)
                .filter(
                    Account.user_id == user_id,
                    Account.plaid_account_id == plaid_acc_id,
                )
                .first()
            )

            if existing:
                # Update existing account
                existing.account_name = normalized.account_name
                existing.official_name = normalized.official_name
                existing.account_subtype = normalized.account_subtype
                existing.mask = normalized.mask
                existing.current_balance = normalized.current_balance
                existing.available_balance = normalized.available_balance
                existing.currency = normalized.currency
                existing.last_balance_update = datetime.now(timezone.utc).replace(tzinfo=None)
                existing.institution_name = row.institution_name
                plaid_account_map[plaid_acc_id] = existing.id
            else:
                # Create new account
                new_acc = Account(
                    user_id=user_id,
                    plaid_connection_id=connection_id,
                    plaid_account_id=plaid_acc_id,
                    account_type=normalized.account_type,
                    account_name=normalized.account_name,
                    official_name=normalized.official_name,
                    account_subtype=normalized.account_subtype,
                    mask=normalized.mask,
                    institution_name=row.institution_name,
                    current_balance=normalized.current_balance,
                    available_balance=normalized.available_balance,
                    currency=normalized.currency,
                    last_balance_update=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                self.db.add(new_acc)
                self.db.flush()
                plaid_account_map[plaid_acc_id] = new_acc.id

        # Step 3: Upsert holdings using normalizer
        holdings_created = 0
        holdings_updated = 0
        upserted_holdings: list[Holding] = []

        for h in response.holdings:
            normalized = normalizer.normalize_holding(h)
            plaid_acc_id = normalized.plaid_account_id
            plaid_sec_id = normalized.plaid_security_id

            # Skip if account wasn't processed (non-investment account)
            if plaid_acc_id not in plaid_account_map:
                continue

            account_id = plaid_account_map[plaid_acc_id]
            security_id = plaid_security_map.get(plaid_sec_id)

            if not security_id:
                continue  # Security not found, skip

            # Try to find existing holding
            existing = (
                self.db.query(Holding)
                .filter(
                    Holding.account_id == account_id,
                    Holding.security_id == security_id,
                )
                .first()
            )

            if existing:
                # Update existing holding
                existing.quantity = normalized.quantity
                existing.cost_basis_per_share = normalized.cost_basis_per_share
                existing.cost_basis_total = normalized.cost_basis_total
                existing.current_price = normalized.institution_price
                existing.current_value = normalized.institution_value
                existing.as_of_date = normalized.as_of_date
                existing.iso_currency_code = normalized.iso_currency_code
                existing.plaid_security_id = plaid_sec_id
                holdings_updated += 1
                upserted_holdings.append(existing)
            else:
                # Create new holding
                new_holding = Holding(
                    account_id=account_id,
                    security_id=security_id,
                    plaid_security_id=plaid_sec_id,
                    quantity=normalized.quantity,
                    cost_basis_per_share=normalized.cost_basis_per_share,
                    cost_basis_total=normalized.cost_basis_total,
                    current_price=normalized.institution_price,
                    current_value=normalized.institution_value,
                    as_of_date=normalized.as_of_date,
                    iso_currency_code=normalized.iso_currency_code,
                )
                self.db.add(new_holding)
                self.db.flush()  # Get the ID for snapshot creation
                holdings_created += 1
                upserted_holdings.append(new_holding)

        # Step 4: Create performance snapshots for all upserted holdings
        snapshots_created = 0
        if upserted_holdings:
            snapshot_service = SnapshotService(self.db)
            snapshots_created = snapshot_service.create_snapshots_for_holdings(upserted_holdings)

        # Step 5: Fetch and store investment transactions using normalizer
        transactions_created = 0
        transactions_updated = 0

        try:
            from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
            from plaid.model.investments_transactions_get_request_options import InvestmentsTransactionsGetRequestOptions
            from datetime import timedelta

            # Fetch last 2 years of transactions
            end_date = date.today()
            start_date = end_date - timedelta(days=730)

            txn_request = InvestmentsTransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                options=InvestmentsTransactionsGetRequestOptions(count=500),
            )
            txn_response = client.investments_transactions_get(txn_request)

            # Also add any new securities from transactions
            for s in txn_response.securities:
                normalized = normalizer.normalize_security(s)
                plaid_sec_id = normalized.plaid_security_id

                if plaid_sec_id not in plaid_security_map:
                    existing = (
                        self.db.query(Security)
                        .filter(Security.plaid_security_id == plaid_sec_id)
                        .first()
                    )
                    if existing:
                        plaid_security_map[plaid_sec_id] = existing.id
                    else:
                        new_sec = Security(
                            ticker=normalized.ticker,
                            plaid_security_id=plaid_sec_id,
                            name=normalized.name,
                            security_type=normalized.security_type,
                            iso_currency_code=normalized.iso_currency_code,
                        )
                        self.db.add(new_sec)
                        self.db.flush()
                        plaid_security_map[plaid_sec_id] = new_sec.id

            # Process transactions using normalizer
            for txn in txn_response.investment_transactions:
                normalized = normalizer.normalize_transaction(txn)
                plaid_acc_id = normalized.plaid_account_id
                plaid_sec_id = normalized.plaid_security_id

                # Skip if account wasn't processed
                if plaid_acc_id not in plaid_account_map:
                    continue

                account_id = plaid_account_map[plaid_acc_id]
                security_id = plaid_security_map.get(plaid_sec_id) if plaid_sec_id else None

                # Skip transactions without security (cash transactions handled separately)
                if not security_id:
                    continue

                plaid_txn_id = normalized.plaid_transaction_id

                # Try to find existing transaction
                existing = (
                    self.db.query(Transaction)
                    .filter(Transaction.plaid_transaction_id == plaid_txn_id)
                    .first()
                )

                if existing:
                    existing.transaction_type = normalized.transaction_type
                    existing.quantity = normalized.quantity
                    existing.amount = normalized.amount
                    existing.price_per_unit = normalized.price_per_unit
                    existing.transaction_date = normalized.transaction_date
                    transactions_updated += 1
                else:
                    new_txn = Transaction(
                        account_id=account_id,
                        security_id=security_id,
                        transaction_type=normalized.transaction_type,
                        quantity=normalized.quantity,
                        amount=normalized.amount,
                        price_per_unit=normalized.price_per_unit,
                        transaction_date=normalized.transaction_date,
                        plaid_transaction_id=plaid_txn_id,
                    )
                    self.db.add(new_txn)
                    transactions_created += 1

        except PlaidApiException:
            # Transactions API might not be available for all connections
            pass

        # Update last_sync_at and sync_status on connection
        row.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)
        row.sync_status = SyncStatus.completed
        row.last_sync_error = None  # Clear any previous error
        self.db.commit()

        return {
            "accounts_synced": len(plaid_account_map),
            "securities_synced": len(plaid_security_map),
            "holdings_created": holdings_created,
            "holdings_updated": holdings_updated,
            "transactions_created": transactions_created,
            "transactions_updated": transactions_updated,
            "snapshots_created": snapshots_created,
        }

    def get_stored_portfolio(self, *, user_id: UUID, connection_id: UUID) -> dict:
        """Fetch stored portfolio data from the database."""
        # Verify connection exists and belongs to user
        connection = (
            self.db.query(PlaidConnection)
            .filter(
                PlaidConnection.id == connection_id,
                PlaidConnection.user_id == user_id,
            )
            .first()
        )
        if not connection:
            raise AppException(
                code="NOT_FOUND",
                message="Plaid connection not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Check sync status explicitly
        if connection.sync_status == SyncStatus.syncing:
            return {
                "status": "syncing",
                "message": "Investment data is being synced. Please wait...",
                "accounts": [],
                "holdings": [],
                "securities": [],
                "last_sync_at": None,
            }
        elif connection.sync_status == SyncStatus.failed:
            return {
                "status": "failed",
                "message": connection.last_sync_error or "Sync failed. Please try again.",
                "accounts": [],
                "holdings": [],
                "securities": [],
                "last_sync_at": None,
            }
        elif connection.sync_status == SyncStatus.pending:
            return {
                "status": "pending",
                "message": "Sync queued. Please wait...",
                "accounts": [],
                "holdings": [],
                "securities": [],
                "last_sync_at": None,
            }

        # Fetch accounts for this connection
        accounts = (
            self.db.query(Account)
            .filter(Account.plaid_connection_id == connection_id)
            .all()
        )

        # Get all account IDs
        account_ids = [acc.id for acc in accounts]

        # Fetch holdings for these accounts
        holdings = []
        security_ids = set()
        if account_ids:
            holding_rows = (
                self.db.query(Holding)
                .filter(Holding.account_id.in_(account_ids))
                .all()
            )
            for h in holding_rows:
                security_ids.add(h.security_id)
                holdings.append(h)

        # Fetch securities
        securities = []
        if security_ids:
            security_rows = (
                self.db.query(Security)
                .filter(Security.id.in_(security_ids))
                .all()
            )
            securities = security_rows

        # Format response
        accounts_data = [
            {
                "account_id": str(acc.id),
                "plaid_account_id": acc.plaid_account_id,
                "name": acc.account_name,
                "official_name": acc.official_name,
                "type": acc.account_type.value if acc.account_type else None,
                "subtype": acc.account_subtype,
                "mask": acc.mask,
                "balances": {
                    "current": float(acc.current_balance) if acc.current_balance is not None else None,
                    "available": float(acc.available_balance) if acc.available_balance is not None else None,
                    "iso_currency_code": acc.currency,
                },
            }
            for acc in accounts
        ]

        # Create security lookup
        security_lookup = {str(s.id): s for s in securities}

        holdings_data = [
            {
                "holding_id": str(h.id),
                "account_id": str(h.account_id),
                "security_id": str(h.security_id),
                "quantity": float(h.quantity) if h.quantity is not None else None,
                "institution_price": float(h.current_price) if h.current_price is not None else None,
                "institution_value": float(h.current_value) if h.current_value is not None else None,
                "cost_basis": float(h.cost_basis_total) if h.cost_basis_total is not None else None,
                "iso_currency_code": h.iso_currency_code,
            }
            for h in holdings
        ]

        securities_data = [
            {
                "security_id": str(s.id),
                "plaid_security_id": s.plaid_security_id,
                "name": s.name,
                "ticker_symbol": s.ticker,
                "type": s.security_type.value if s.security_type else None,
                "close_price": float(s.close_price) if s.close_price is not None else None,
                "close_price_as_of": s.close_price_as_of.isoformat() if s.close_price_as_of else None,
                "iso_currency_code": s.iso_currency_code,
            }
            for s in securities
        ]

        return {
            "status": "ready",
            "accounts": accounts_data,
            "holdings": holdings_data,
            "securities": securities_data,
            "last_sync_at": connection.last_sync_at.isoformat() + "Z" if connection.last_sync_at else None,
        }

    def get_sync_status(self, *, user_id: UUID, connection_id: UUID) -> dict:
        """Get sync status for a connection."""
        connection = (
            self.db.query(PlaidConnection)
            .filter(
                PlaidConnection.id == connection_id,
                PlaidConnection.user_id == user_id,
            )
            .first()
        )
        if not connection:
            raise AppException(
                code="NOT_FOUND",
                message="Plaid connection not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Check if we have any accounts for this connection
        account_count = (
            self.db.query(Account)
            .filter(Account.plaid_connection_id == connection_id)
            .count()
        )

        # Use explicit sync_status field
        status_value = connection.sync_status.value if connection.sync_status else "pending"

        return {
            "status": status_value,
            "last_sync_at": connection.last_sync_at.isoformat() + "Z" if connection.last_sync_at else None,
            "error": connection.last_sync_error,
            "accounts_count": account_count,
        }

    def get_accounts(self, *, user_id: UUID) -> dict:
        """Get all accounts for a user."""
        accounts = (
            self.db.query(Account)
            .filter(Account.user_id == user_id)
            .order_by(Account.account_name)
            .all()
        )

        accounts_data = [
            {
                "id": str(acc.id),
                "plaid_account_id": acc.plaid_account_id,
                "name": acc.account_name,
                "official_name": acc.official_name,
                "type": acc.account_type.value if acc.account_type else None,
                "subtype": acc.account_subtype,
                "mask": acc.mask,
                "institution_name": acc.institution_name,
                "balance": float(acc.current_balance) if acc.current_balance is not None else None,
                "available_balance": float(acc.available_balance) if acc.available_balance is not None else None,
                "currency": acc.currency,
                "last_updated": acc.last_balance_update.isoformat() + "Z" if acc.last_balance_update else None,
            }
            for acc in accounts
        ]

        return {"accounts": accounts_data}

    def get_holdings(self, *, user_id: UUID) -> dict:
        """Get all holdings for a user with security details."""
        # Get all accounts for the user
        account_ids = [
            acc.id for acc in
            self.db.query(Account.id).filter(Account.user_id == user_id).all()
        ]

        if not account_ids:
            return {"holdings": [], "total_value": 0, "total_gain_loss": 0}

        # Get holdings with account info
        holdings = (
            self.db.query(Holding)
            .filter(Holding.account_id.in_(account_ids))
            .all()
        )

        # Get all security IDs
        security_ids = {h.security_id for h in holdings}

        # Fetch securities
        securities = {}
        if security_ids:
            for sec in self.db.query(Security).filter(Security.id.in_(security_ids)).all():
                securities[sec.id] = sec

        # Get account names
        accounts = {}
        for acc in self.db.query(Account).filter(Account.id.in_(account_ids)).all():
            accounts[acc.id] = acc

        holdings_data = []
        total_value = 0
        total_cost_basis = 0

        for h in holdings:
            sec = securities.get(h.security_id)
            acc = accounts.get(h.account_id)

            value = float(h.current_value) if h.current_value is not None else 0
            cost_basis = float(h.cost_basis_total) if h.cost_basis_total is not None else None
            gain_loss = (value - cost_basis) if cost_basis is not None else None

            total_value += value
            if cost_basis is not None:
                total_cost_basis += cost_basis

            holdings_data.append({
                "id": str(h.id),
                "account_id": str(h.account_id),
                "account_name": acc.account_name if acc else None,
                "ticker": sec.ticker if sec else None,
                "name": sec.name if sec else "Unknown Security",
                "security_type": sec.security_type.value if sec and sec.security_type else None,
                "quantity": float(h.quantity) if h.quantity is not None else None,
                "price": float(h.current_price) if h.current_price is not None else None,
                "value": value,
                "cost_basis": cost_basis,
                "gain_loss": gain_loss,
                "gain_loss_percent": ((gain_loss / cost_basis) * 100) if gain_loss is not None and cost_basis and cost_basis != 0 else None,
                "currency": h.iso_currency_code or "USD",
            })

        total_gain_loss = total_value - total_cost_basis if total_cost_basis > 0 else None

        return {
            "holdings": holdings_data,
            "total_value": total_value,
            "total_cost_basis": total_cost_basis if total_cost_basis > 0 else None,
            "total_gain_loss": total_gain_loss,
            "total_gain_loss_percent": ((total_gain_loss / total_cost_basis) * 100) if total_gain_loss is not None and total_cost_basis > 0 else None,
        }

    def get_transactions(
        self,
        *,
        user_id: UUID,
        limit: int = 50,
        page: int = 0,
        account_id: UUID | None = None,
    ) -> dict:
        """Get investment transactions for a user with pagination."""
        # Get all accounts for the user
        account_query = self.db.query(Account.id).filter(Account.user_id == user_id)
        if account_id:
            account_query = account_query.filter(Account.id == account_id)
        account_ids = [acc.id for acc in account_query.all()]

        if not account_ids:
            return {"transactions": [], "total_count": 0, "page": page, "limit": limit, "total_pages": 0}

        # Count total transactions
        total_count = (
            self.db.query(Transaction)
            .filter(Transaction.account_id.in_(account_ids))
            .count()
        )

        # Calculate offset from page number
        offset = page * limit

        # Get transactions ordered by date descending with pagination
        transactions = (
            self.db.query(Transaction)
            .filter(Transaction.account_id.in_(account_ids))
            .order_by(Transaction.transaction_date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Get all security IDs
        security_ids = {t.security_id for t in transactions if t.security_id}

        # Fetch securities
        securities = {}
        if security_ids:
            for sec in self.db.query(Security).filter(Security.id.in_(security_ids)).all():
                securities[sec.id] = sec

        # Get account names
        accounts = {}
        for acc in self.db.query(Account).filter(Account.id.in_(account_ids)).all():
            accounts[acc.id] = acc

        transactions_data = []
        for t in transactions:
            sec = securities.get(t.security_id) if t.security_id else None
            acc = accounts.get(t.account_id)

            transactions_data.append({
                "id": str(t.id),
                "account_id": str(t.account_id),
                "account_name": acc.account_name if acc else None,
                "ticker": sec.ticker if sec else None,
                "security_name": sec.name if sec else None,
                "type": t.transaction_type.value if t.transaction_type else None,
                "quantity": float(t.quantity) if t.quantity is not None else None,
                "price": float(t.price_per_unit) if t.price_per_unit is not None else None,
                "amount": float(t.amount) if t.amount is not None else None,
                "date": t.transaction_date.isoformat() if t.transaction_date else None,
                "settlement_date": t.settlement_date.isoformat() if t.settlement_date else None,
            })

        # Calculate total pages
        total_pages = (total_count + limit - 1) // limit  # Ceiling division

        return {
            "transactions": transactions_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }

    def get_holding_history(
        self,
        *,
        user_id: UUID,
        holding_id: UUID,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 365,
    ) -> dict:
        """Get performance history for a specific holding.

        Args:
            user_id: ID of the user (for authorization)
            holding_id: ID of the holding
            start_date: Start of date range (optional)
            end_date: End of date range (optional)
            limit: Maximum number of snapshots to return

        Returns:
            Dict with holding metadata and snapshots list
        """
        # Verify holding belongs to user by joining through Account
        holding = (
            self.db.query(Holding)
            .join(Account, Holding.account_id == Account.id)
            .filter(
                Holding.id == holding_id,
                Account.user_id == user_id,
            )
            .first()
        )

        if not holding:
            raise AppException(
                code="NOT_FOUND",
                message="Holding not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Get security and account details for metadata
        security = (
            self.db.query(Security)
            .filter(Security.id == holding.security_id)
            .first()
        )
        account = (
            self.db.query(Account)
            .filter(Account.id == holding.account_id)
            .first()
        )

        # Get snapshots using SnapshotService
        snapshot_service = SnapshotService(self.db)
        snapshots = snapshot_service.get_holding_history(
            holding_id=holding_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

        # Get date range
        earliest_date, latest_date = snapshot_service.get_date_range_for_holding(holding_id)

        # Format snapshots
        snapshots_data = [
            {
                "id": str(s.id),
                "snapshot_date": s.snapshot_date.isoformat(),
                "value": float(s.value),
                "cost_basis_total": float(s.cost_basis_total) if s.cost_basis_total is not None else None,
                "unrealized_gain": float(s.unrealized_gain) if s.unrealized_gain is not None else None,
                "unrealized_gain_pct": float(s.unrealized_gain_pct) if s.unrealized_gain_pct is not None else None,
            }
            for s in snapshots
        ]

        return {
            "holding_id": str(holding_id),
            "ticker": security.ticker if security else None,
            "security_name": security.name if security else "Unknown Security",
            "account_name": account.account_name if account else None,
            "snapshots": snapshots_data,
            "earliest_date": earliest_date.isoformat() if earliest_date else None,
            "latest_date": latest_date.isoformat() if latest_date else None,
        }

    def _map_plaid_banking_account_type(self, plaid_type: str | None, plaid_subtype: str | None) -> AccountType:
        """Map Plaid depository/credit account type/subtype to our AccountType enum."""
        subtype = (plaid_subtype or "").lower().replace(" ", "_")
        ptype = (plaid_type or "").lower()

        if subtype == "checking":
            return AccountType.checking
        if subtype == "savings":
            return AccountType.savings
        if subtype in ("money_market", "money market"):
            return AccountType.money_market
        if subtype == "cd":
            return AccountType.cd
        if ptype == "credit" or subtype == "credit_card":
            return AccountType.credit_card
        return AccountType.other

    def sync_transactions(self, *, user_id: UUID, connection_id: UUID) -> dict:
        """Sync banking transactions using Plaid's /transactions/sync endpoint."""
        from plaid.model.transactions_sync_request import TransactionsSyncRequest

        row = (
            self.db.query(PlaidConnection)
            .filter(
                PlaidConnection.id == connection_id,
                PlaidConnection.user_id == user_id,
            )
            .first()
        )
        if not row:
            raise AppException(
                code="NOT_FOUND",
                message="Plaid connection not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if row.status != PlaidConnectionStatus.active:
            raise AppException(
                code="VALIDATION_ERROR",
                message="Plaid connection is not active.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        access_token = decrypt_email(row.access_token_encrypted)
        if not access_token:
            raise AppException(
                code="CONFIG_ERROR",
                message="Could not decrypt stored Plaid token.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        from app.plaid.client import get_plaid_client

        client = get_plaid_client()

        added_count = 0
        modified_count = 0
        removed_count = 0
        cursor = row.transaction_cursor
        has_more = True

        while has_more:
            try:
                request_kwargs = {"access_token": access_token}
                if cursor:
                    request_kwargs["cursor"] = cursor
                request = TransactionsSyncRequest(**request_kwargs)
                response = client.transactions_sync(request)
            except PlaidApiException as e:
                err_code: str | None = None
                if isinstance(e.body, (bytes, bytearray)):
                    try:
                        parsed = json.loads(e.body.decode())
                        if isinstance(parsed, dict):
                            err_code = str(parsed.get("error_code") or "")
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass

                if err_code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                    cursor = None
                    continue

                if err_code == "ITEM_LOGIN_REQUIRED":
                    raise AppException(
                        code="PLAID_REAUTH_REQUIRED",
                        message="Bank login expired. Please reconnect your account.",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    ) from e

                raise AppException(
                    code="PLAID_ERROR",
                    message=_plaid_error_message(e),
                    status_code=status.HTTP_502_BAD_GATEWAY,
                ) from e

            # Upsert accounts if needed
            for acc in response.accounts:
                plaid_acc_id = acc.account_id
                acc_type_val = acc.type.value if hasattr(acc.type, "value") else acc.type
                acc_subtype_val = acc.subtype.value if hasattr(acc.subtype, "value") else acc.subtype

                # Skip investment accounts (handled by ingest_investments)
                if acc_type_val == "investment":
                    continue

                existing = (
                    self.db.query(Account)
                    .filter(
                        Account.user_id == user_id,
                        Account.plaid_account_id == plaid_acc_id,
                    )
                    .first()
                )

                if existing:
                    existing.account_name = acc.name
                    existing.official_name = getattr(acc, "official_name", None)
                    existing.account_subtype = acc_subtype_val
                    existing.mask = acc.mask
                    existing.current_balance = float(acc.balances.current) if acc.balances.current is not None else None
                    existing.available_balance = float(acc.balances.available) if acc.balances.available is not None else None
                    existing.currency = acc.balances.iso_currency_code or "USD"
                    existing.last_balance_update = datetime.now(timezone.utc).replace(tzinfo=None)
                    existing.institution_name = row.institution_name
                else:
                    account_type = self._map_plaid_banking_account_type(acc_type_val, acc_subtype_val)
                    new_acc = Account(
                        user_id=user_id,
                        plaid_connection_id=connection_id,
                        plaid_account_id=plaid_acc_id,
                        account_type=account_type,
                        account_name=acc.name,
                        official_name=getattr(acc, "official_name", None),
                        account_subtype=acc_subtype_val,
                        mask=acc.mask,
                        institution_name=row.institution_name,
                        current_balance=float(acc.balances.current) if acc.balances.current is not None else None,
                        available_balance=float(acc.balances.available) if acc.balances.available is not None else None,
                        currency=acc.balances.iso_currency_code or "USD",
                        last_balance_update=datetime.now(timezone.utc).replace(tzinfo=None),
                    )
                    self.db.add(new_acc)
                    self.db.flush()

            # Build account lookup
            plaid_account_map: dict[str, UUID] = {}
            accounts = (
                self.db.query(Account)
                .filter(Account.plaid_connection_id == connection_id)
                .all()
            )
            for acc in accounts:
                plaid_account_map[acc.plaid_account_id] = acc.id

            # Process added transactions
            for txn in response.added:
                account_id = plaid_account_map.get(txn.account_id)
                if not account_id:
                    continue
                self._upsert_banking_transaction(account_id, txn)
                added_count += 1

            # Process modified transactions
            for txn in response.modified:
                account_id = plaid_account_map.get(txn.account_id)
                if not account_id:
                    continue
                self._upsert_banking_transaction(account_id, txn)
                modified_count += 1

            # Process removed transactions
            for txn_id in response.removed:
                plaid_txn_id = txn_id.transaction_id if hasattr(txn_id, "transaction_id") else txn_id
                self._remove_banking_transaction(plaid_txn_id)
                removed_count += 1

            cursor = response.next_cursor
            has_more = response.has_more

        # Persist cursor
        row.transaction_cursor = cursor
        row.transaction_cursor_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.commit()

        return {
            "added": added_count,
            "modified": modified_count,
            "removed": removed_count,
            "has_more": False,
            "cursor": cursor,
        }

    def _upsert_banking_transaction(self, account_id: UUID, txn) -> None:
        """Insert or update a banking transaction."""
        plaid_txn_id = txn.transaction_id

        existing = (
            self.db.query(BankingTransaction)
            .filter(BankingTransaction.plaid_transaction_id == plaid_txn_id)
            .first()
        )

        category = None
        if hasattr(txn, "category") and txn.category:
            category = list(txn.category) if not isinstance(txn.category, list) else txn.category

        personal_finance_category = None
        if hasattr(txn, "personal_finance_category") and txn.personal_finance_category:
            pfc = txn.personal_finance_category
            personal_finance_category = {
                "primary": pfc.primary if hasattr(pfc, "primary") else None,
                "detailed": pfc.detailed if hasattr(pfc, "detailed") else None,
            }

        location_json = None
        if hasattr(txn, "location") and txn.location:
            loc = txn.location
            location_json = {
                "address": loc.address if hasattr(loc, "address") else None,
                "city": loc.city if hasattr(loc, "city") else None,
                "region": loc.region if hasattr(loc, "region") else None,
                "postal_code": loc.postal_code if hasattr(loc, "postal_code") else None,
                "country": loc.country if hasattr(loc, "country") else None,
                "lat": float(loc.lat) if hasattr(loc, "lat") and loc.lat is not None else None,
                "lon": float(loc.lon) if hasattr(loc, "lon") and loc.lon is not None else None,
            }

        payment_channel = None
        if hasattr(txn, "payment_channel") and txn.payment_channel:
            payment_channel = txn.payment_channel.value if hasattr(txn.payment_channel, "value") else txn.payment_channel

        if existing:
            existing.amount = float(txn.amount)
            existing.iso_currency_code = txn.iso_currency_code
            existing.category = category
            existing.personal_finance_category = personal_finance_category
            existing.merchant_name = txn.merchant_name
            existing.name = txn.name or "Unknown Transaction"
            existing.transaction_date = txn.date
            existing.authorized_date = txn.authorized_date
            existing.pending = txn.pending if hasattr(txn, "pending") else False
            existing.payment_channel = payment_channel
            existing.location_json = location_json
        else:
            new_txn = BankingTransaction(
                account_id=account_id,
                plaid_transaction_id=plaid_txn_id,
                amount=float(txn.amount),
                iso_currency_code=txn.iso_currency_code,
                category=category,
                personal_finance_category=personal_finance_category,
                merchant_name=txn.merchant_name,
                name=txn.name or "Unknown Transaction",
                transaction_date=txn.date,
                authorized_date=txn.authorized_date,
                pending=txn.pending if hasattr(txn, "pending") else False,
                payment_channel=payment_channel,
                location_json=location_json,
            )
            self.db.add(new_txn)

    def _remove_banking_transaction(self, plaid_transaction_id: str) -> None:
        """Remove a banking transaction by Plaid transaction ID."""
        self.db.query(BankingTransaction).filter(
            BankingTransaction.plaid_transaction_id == plaid_transaction_id
        ).delete()

    def handle_webhook(self, *, webhook_type: str, webhook_code: str, item_id: str) -> dict:
        """Handle incoming Plaid webhook."""
        logger.info("Received Plaid webhook: type=%s, code=%s, item_id=%s", webhook_type, webhook_code, item_id)

        connection = (
            self.db.query(PlaidConnection)
            .filter(PlaidConnection.plaid_item_id == item_id)
            .first()
        )

        if not connection:
            logger.warning("Webhook ignored - unknown item_id: %s", item_id)
            return {"status": "ignored", "reason": "unknown_item"}

        if webhook_type == "TRANSACTIONS" and webhook_code == "SYNC_UPDATES_AVAILABLE":
            # Trigger a sync for this connection
            try:
                result = self.sync_transactions(
                    user_id=connection.user_id,
                    connection_id=connection.id,
                )
                logger.info("Transaction sync completed for connection %s", connection.id)
                return {"status": "processed", "result": result}
            except Exception as e:
                logger.exception("Transaction sync failed for connection %s: %s", connection.id, e)
                return {"status": "error", "reason": "sync_failed", "detail": str(e)[:200]}

        if webhook_type == "TRANSACTIONS" and webhook_code in ("INITIAL_UPDATE", "HISTORICAL_UPDATE"):
            # Initial sync - trigger sync
            try:
                result = self.sync_transactions(
                    user_id=connection.user_id,
                    connection_id=connection.id,
                )
                logger.info("Initial transaction sync completed for connection %s", connection.id)
                return {"status": "processed", "result": result}
            except Exception as e:
                logger.exception("Initial transaction sync failed for connection %s: %s", connection.id, e)
                return {"status": "error", "reason": "sync_failed", "detail": str(e)[:200]}

        # Handle HOLDINGS webhooks for investment updates
        if webhook_type == "HOLDINGS" and webhook_code == "DEFAULT_UPDATE":
            try:
                self.ingest_investments(
                    user_id=connection.user_id,
                    connection_id=connection.id,
                )
                logger.info("Investment sync completed for connection %s", connection.id)
                return {"status": "processed", "result": {"investments_synced": True}}
            except Exception as e:
                logger.exception("Investment sync failed for connection %s: %s", connection.id, e)
                return {"status": "error", "reason": "investment_sync_failed", "detail": str(e)[:200]}

        # Handle INVESTMENTS_TRANSACTIONS webhooks
        if webhook_type == "INVESTMENTS_TRANSACTIONS" and webhook_code == "DEFAULT_UPDATE":
            try:
                self.ingest_investments(
                    user_id=connection.user_id,
                    connection_id=connection.id,
                )
                logger.info("Investment transactions sync completed for connection %s", connection.id)
                return {"status": "processed", "result": {"investments_synced": True}}
            except Exception as e:
                logger.exception("Investment transactions sync failed for connection %s: %s", connection.id, e)
                return {"status": "error", "reason": "investment_sync_failed", "detail": str(e)[:200]}

        return {"status": "ignored", "reason": f"unhandled_{webhook_type}_{webhook_code}"}

    def get_banking_transactions(
        self,
        *,
        user_id: UUID,
        limit: int = 50,
        page: int = 0,
        account_id: UUID | None = None,
    ) -> dict:
        """Get banking transactions for a user with pagination."""
        # Get all accounts for the user
        account_query = self.db.query(Account.id).filter(Account.user_id == user_id)
        if account_id:
            account_query = account_query.filter(Account.id == account_id)
        account_ids = [acc.id for acc in account_query.all()]

        if not account_ids:
            return {"transactions": [], "total_count": 0, "page": page, "limit": limit, "total_pages": 0}

        # Count total
        total_count = (
            self.db.query(BankingTransaction)
            .filter(BankingTransaction.account_id.in_(account_ids))
            .count()
        )

        # Calculate offset from page number
        offset = page * limit

        # Fetch transactions
        transactions = (
            self.db.query(BankingTransaction)
            .filter(BankingTransaction.account_id.in_(account_ids))
            .order_by(BankingTransaction.transaction_date.desc(), BankingTransaction.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        transactions_data = [
            {
                "id": str(t.id),
                "account_id": str(t.account_id),
                "plaid_transaction_id": t.plaid_transaction_id,
                "amount": float(t.amount),
                "iso_currency_code": t.iso_currency_code,
                "category": t.category,
                "personal_finance_category": t.personal_finance_category,
                "merchant_name": t.merchant_name,
                "name": t.name,
                "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                "authorized_date": t.authorized_date.isoformat() if t.authorized_date else None,
                "pending": t.pending,
                "payment_channel": t.payment_channel,
                "location": t.location_json,
            }
            for t in transactions
        ]

        # Calculate total pages
        total_pages = (total_count + limit - 1) // limit  # Ceiling division

        return {
            "transactions": transactions_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }
