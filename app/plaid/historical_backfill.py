"""Historical portfolio snapshot backfill service.

This module provides services to reconstruct historical portfolio state
from transaction history and create backfilled snapshots.

Algorithm: Reverse Transaction Reconstruction
- Start with current holdings
- Work backwards by reversing transactions
- For each historical date, calculate what the holding would have been
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    AccountSnapshot,
    BackfillStatus,
    Holding,
    PerformanceSnapshot,
    PlaidConnection,
    Security,
    Transaction,
    TransactionType,
)

logger = logging.getLogger(__name__)


@dataclass
class ReconstructedState:
    """Represents reconstructed holding state at a specific date."""
    holding_id: UUID
    security_id: UUID
    target_date: date
    quantity: float
    price: float
    value: float
    cost_basis_total: Optional[float]


@dataclass
class BackfillResult:
    """Result of a backfill operation."""
    connection_id: UUID
    status: str
    snapshots_created: int
    account_snapshots_created: int
    days_processed: int
    earliest_date: Optional[date]
    latest_date: Optional[date]
    error_message: Optional[str] = None


class HistoricalBackfillService:
    """Service for reconstructing historical portfolio snapshots from transactions.

    The service works by:
    1. Getting current holdings for a connection
    2. Getting all transactions for those holdings
    3. For each date going backwards, reversing transactions to reconstruct state
    4. Creating performance snapshots for each reconstructed state
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.batch_size = 30  # Days to process per commit

    def backfill_connection(
        self,
        connection_id: UUID,
        days_back: int = 730,
    ) -> BackfillResult:
        """Main entry point: backfill historical snapshots for all accounts in a connection.

        Args:
            connection_id: ID of the PlaidConnection to backfill
            days_back: Number of days to look back (default 2 years)

        Returns:
            BackfillResult with status and counts
        """
        connection = (
            self.db.query(PlaidConnection)
            .filter(PlaidConnection.id == connection_id)
            .first()
        )

        if not connection:
            return BackfillResult(
                connection_id=connection_id,
                status="failed",
                snapshots_created=0,
                account_snapshots_created=0,
                days_processed=0,
                earliest_date=None,
                latest_date=None,
                error_message="Connection not found",
            )

        # Update backfill status to in_progress
        connection.backfill_status = BackfillStatus.in_progress
        connection.backfill_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.backfill_error = None
        self.db.commit()

        try:
            # Get all accounts for this connection
            accounts = (
                self.db.query(Account)
                .filter(Account.plaid_connection_id == connection_id)
                .all()
            )

            if not accounts:
                connection.backfill_status = BackfillStatus.completed
                connection.backfill_completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                self.db.commit()
                return BackfillResult(
                    connection_id=connection_id,
                    status="completed",
                    snapshots_created=0,
                    account_snapshots_created=0,
                    days_processed=0,
                    earliest_date=None,
                    latest_date=None,
                )

            end_date = date.today()
            start_date = end_date - timedelta(days=days_back)

            total_snapshots = 0
            total_account_snapshots = 0
            earliest_date = None
            latest_date = end_date

            for account in accounts:
                holding_snapshots, account_snaps, earliest = self.backfill_account(
                    account_id=account.id,
                    start_date=start_date,
                    end_date=end_date,
                )
                total_snapshots += holding_snapshots
                total_account_snapshots += account_snaps
                if earliest and (earliest_date is None or earliest < earliest_date):
                    earliest_date = earliest

            # Update connection status
            connection.backfill_status = BackfillStatus.completed
            connection.backfill_completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            connection.backfill_snapshots_created = total_snapshots
            self.db.commit()

            logger.info(
                "Backfill completed for connection %s: %d holding snapshots, %d account snapshots",
                connection_id,
                total_snapshots,
                total_account_snapshots,
            )

            return BackfillResult(
                connection_id=connection_id,
                status="completed",
                snapshots_created=total_snapshots,
                account_snapshots_created=total_account_snapshots,
                days_processed=days_back,
                earliest_date=earliest_date,
                latest_date=latest_date,
            )

        except Exception as e:
            logger.exception("Backfill failed for connection %s: %s", connection_id, e)
            self.db.rollback()
            connection = (
                self.db.query(PlaidConnection)
                .filter(PlaidConnection.id == connection_id)
                .first()
            )
            if connection:
                connection.backfill_status = BackfillStatus.failed
                connection.backfill_error = str(e)[:500]
                self.db.commit()

            return BackfillResult(
                connection_id=connection_id,
                status="failed",
                snapshots_created=0,
                account_snapshots_created=0,
                days_processed=0,
                earliest_date=None,
                latest_date=None,
                error_message=str(e)[:500],
            )

    def backfill_account(
        self,
        account_id: UUID,
        start_date: date,
        end_date: date,
    ) -> tuple[int, int, Optional[date]]:
        """Backfill snapshots for all holdings in an account.

        Args:
            account_id: ID of the account
            start_date: Earliest date to backfill
            end_date: Latest date (usually today)

        Returns:
            Tuple of (holding_snapshots_created, account_snapshots_created, earliest_transaction_date)
        """
        # Get all holdings for this account
        holdings = (
            self.db.query(Holding)
            .filter(Holding.account_id == account_id)
            .all()
        )

        if not holdings:
            return 0, 0, None

        # Get account for balance snapshots
        account = self.db.query(Account).filter(Account.id == account_id).first()

        total_snapshots = 0
        earliest_transaction_date = None

        for holding in holdings:
            # Get all transactions for this holding's security in this account
            transactions = (
                self.db.query(Transaction)
                .filter(
                    Transaction.account_id == account_id,
                    Transaction.security_id == holding.security_id,
                )
                .order_by(Transaction.transaction_date.desc())
                .all()
            )

            # Extract prices from transactions
            price_history = self.extract_prices_from_transactions(
                holding.security_id,
                transactions,
            )

            # Get the current price for forward-fill fallback
            security = (
                self.db.query(Security)
                .filter(Security.id == holding.security_id)
                .first()
            )
            current_price = float(holding.current_price) if holding.current_price else None
            if current_price is None and security and security.close_price:
                current_price = float(security.close_price)

            # Determine the date range to process
            if transactions:
                earliest_txn = min(t.transaction_date for t in transactions)
                if earliest_transaction_date is None or earliest_txn < earliest_transaction_date:
                    earliest_transaction_date = earliest_txn
                # Only backfill to earliest transaction date or start_date, whichever is later
                actual_start = max(start_date, earliest_txn)
            else:
                # No transactions - use current holdings as-is for recent dates only
                actual_start = end_date - timedelta(days=30)

            # Forward-fill prices
            filled_prices = self.forward_fill_prices(
                price_history,
                actual_start,
                end_date,
                current_price,
            )

            # Process in batches
            snapshots_created = self._process_holding_backfill(
                holding=holding,
                transactions=transactions,
                prices=filled_prices,
                start_date=actual_start,
                end_date=end_date,
            )
            total_snapshots += snapshots_created

        # Create account balance snapshots based on holding snapshots
        account_snapshots = 0
        if account:
            account_snapshots = self._backfill_account_snapshots(
                account=account,
                start_date=start_date if earliest_transaction_date is None else max(start_date, earliest_transaction_date),
                end_date=end_date,
            )

        return total_snapshots, account_snapshots, earliest_transaction_date

    def _process_holding_backfill(
        self,
        holding: Holding,
        transactions: list[Transaction],
        prices: dict[date, float],
        start_date: date,
        end_date: date,
    ) -> int:
        """Process backfill for a single holding.

        Args:
            holding: The holding to backfill
            transactions: All transactions for this holding
            prices: Price history (date -> price)
            start_date: Start date for backfill
            end_date: End date for backfill

        Returns:
            Number of snapshots created
        """
        snapshots_created = 0
        batch_count = 0

        current_date = end_date
        while current_date >= start_date:
            # Reconstruct state at this date
            state = self.reconstruct_holding_at_date(
                holding=holding,
                transactions=transactions,
                target_date=current_date,
                price=prices.get(current_date),
            )

            if state and state.quantity > 0:
                # Create snapshot
                self._create_performance_snapshot(state)
                snapshots_created += 1
                batch_count += 1

            # Commit in batches
            if batch_count >= self.batch_size:
                self.db.commit()
                batch_count = 0

            current_date -= timedelta(days=1)

        # Final commit
        if batch_count > 0:
            self.db.commit()

        return snapshots_created

    def reconstruct_holding_at_date(
        self,
        holding: Holding,
        transactions: list[Transaction],
        target_date: date,
        price: Optional[float],
    ) -> Optional[ReconstructedState]:
        """Core algorithm: reverse transactions to get holding state at a past date.

        Args:
            holding: Current holding state
            transactions: All transactions for this holding (ordered by date desc)
            target_date: The date to reconstruct state for
            price: Price to use for valuation

        Returns:
            ReconstructedState if the holding existed, None otherwise
        """
        if price is None:
            return None

        # Start with current quantity
        quantity = float(holding.quantity) if holding.quantity else 0.0
        cost_basis_total = float(holding.cost_basis_total) if holding.cost_basis_total else None

        # Reverse all transactions that occurred AFTER the target date
        for txn in transactions:
            if txn.transaction_date <= target_date:
                # This transaction was before or on target date, stop processing
                continue

            txn_qty = float(txn.quantity) if txn.quantity else 0.0
            txn_type = txn.transaction_type

            if txn_type == TransactionType.buy:
                # Reverse a buy: we didn't have these shares before the buy
                quantity -= txn_qty
            elif txn_type == TransactionType.sell:
                # Reverse a sell: we had these shares before selling
                quantity += txn_qty
            elif txn_type == TransactionType.transfer_in:
                # Reverse transfer in: we didn't have these before
                quantity -= txn_qty
            elif txn_type == TransactionType.transfer_out:
                # Reverse transfer out: we had these before
                quantity += txn_qty
            elif txn_type == TransactionType.split:
                # Handle stock splits - reverse the split ratio
                # Split transactions typically have the post-split quantity
                # We need the original transaction data to properly reverse
                # For now, skip splits as they require additional metadata
                pass

        # If quantity is negative or zero, the holding didn't exist yet
        if quantity <= 0:
            return None

        value = quantity * price

        # Adjust cost basis proportionally if we have it
        adjusted_cost_basis = None
        if cost_basis_total is not None and holding.quantity and float(holding.quantity) > 0:
            cost_per_share = cost_basis_total / float(holding.quantity)
            adjusted_cost_basis = cost_per_share * quantity

        return ReconstructedState(
            holding_id=holding.id,
            security_id=holding.security_id,
            target_date=target_date,
            quantity=quantity,
            price=price,
            value=value,
            cost_basis_total=adjusted_cost_basis,
        )

    def extract_prices_from_transactions(
        self,
        security_id: UUID,
        transactions: list[Transaction],
    ) -> dict[date, float]:
        """Extract price history from transaction records.

        Args:
            security_id: ID of the security
            transactions: Transactions to extract prices from

        Returns:
            Dict mapping dates to prices
        """
        prices: dict[date, float] = {}

        for txn in transactions:
            if txn.price_per_unit is not None and txn.price_per_unit > 0:
                prices[txn.transaction_date] = float(txn.price_per_unit)

        return prices

    def forward_fill_prices(
        self,
        prices: dict[date, float],
        start_date: date,
        end_date: date,
        fallback_price: Optional[float] = None,
    ) -> dict[date, float]:
        """Forward-fill price gaps using last known price.

        Args:
            prices: Known prices by date
            start_date: Start of date range
            end_date: End of date range
            fallback_price: Price to use if no historical data

        Returns:
            Complete price history with gaps filled
        """
        filled: dict[date, float] = {}
        current_date = start_date
        last_known_price = fallback_price

        # Sort known prices by date
        sorted_dates = sorted(prices.keys())

        # Find the first known price before or at start_date
        for d in sorted_dates:
            if d <= start_date:
                last_known_price = prices[d]
            else:
                break

        while current_date <= end_date:
            if current_date in prices:
                last_known_price = prices[current_date]

            if last_known_price is not None:
                filled[current_date] = last_known_price

            current_date += timedelta(days=1)

        return filled

    def _create_performance_snapshot(self, state: ReconstructedState) -> None:
        """Create a performance snapshot using upsert.

        Args:
            state: Reconstructed holding state
        """
        unrealized_gain = None
        unrealized_gain_pct = None

        if state.cost_basis_total is not None and state.cost_basis_total != 0:
            unrealized_gain = state.value - state.cost_basis_total
            unrealized_gain_pct = (unrealized_gain / state.cost_basis_total) * 100
            unrealized_gain_pct = max(-9999.99, min(9999.99, unrealized_gain_pct))

        stmt = insert(PerformanceSnapshot).values(
            holding_id=state.holding_id,
            snapshot_date=state.target_date,
            value=state.value,
            cost_basis_total=state.cost_basis_total,
            unrealized_gain=unrealized_gain,
            unrealized_gain_pct=unrealized_gain_pct,
        )

        # On conflict, update only if the existing snapshot was created by backfill
        # (we don't want to overwrite "real" daily snapshots)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_perf_snapshot_holding_date",
        )

        self.db.execute(stmt)

    def _backfill_account_snapshots(
        self,
        account: Account,
        start_date: date,
        end_date: date,
    ) -> int:
        """Create account balance snapshots by summing holding values.

        This is a simplified approach - we sum the holding snapshot values
        for each date to get the account balance.

        Args:
            account: Account to create snapshots for
            start_date: Start date
            end_date: End date

        Returns:
            Number of account snapshots created
        """
        from sqlalchemy import func

        snapshots_created = 0
        batch_count = 0
        current_date = end_date

        while current_date >= start_date:
            # Get sum of holding values for this account on this date
            holdings = (
                self.db.query(Holding)
                .filter(Holding.account_id == account.id)
                .all()
            )

            total_value = 0.0
            has_data = False

            for holding in holdings:
                snapshot = (
                    self.db.query(PerformanceSnapshot)
                    .filter(
                        PerformanceSnapshot.holding_id == holding.id,
                        PerformanceSnapshot.snapshot_date == current_date,
                    )
                    .first()
                )
                if snapshot:
                    total_value += float(snapshot.value)
                    has_data = True

            if has_data and total_value > 0:
                # Create account snapshot
                stmt = insert(AccountSnapshot).values(
                    account_id=account.id,
                    snapshot_date=current_date,
                    current_balance=total_value,
                    available_balance=None,
                    currency=account.currency or "USD",
                )

                stmt = stmt.on_conflict_do_nothing(
                    constraint="uq_account_snapshot_account_date",
                )

                self.db.execute(stmt)
                snapshots_created += 1
                batch_count += 1

            if batch_count >= self.batch_size:
                self.db.commit()
                batch_count = 0

            current_date -= timedelta(days=1)

        if batch_count > 0:
            self.db.commit()

        return snapshots_created

    def get_backfill_status(self, connection_id: UUID) -> dict:
        """Get the current backfill status for a connection.

        Args:
            connection_id: ID of the PlaidConnection

        Returns:
            Dict with status information
        """
        connection = (
            self.db.query(PlaidConnection)
            .filter(PlaidConnection.id == connection_id)
            .first()
        )

        if not connection:
            return {
                "status": "not_found",
                "progress_percent": 0,
                "snapshots_created": 0,
                "error_message": "Connection not found",
            }

        # Calculate progress based on status
        progress_percent = 0.0
        if connection.backfill_status == BackfillStatus.pending:
            progress_percent = 0.0
        elif connection.backfill_status == BackfillStatus.in_progress:
            # Estimate progress based on snapshots created
            # Rough estimate: 20 holdings * 730 days = 14,600 snapshots
            estimated_total = 14600
            progress_percent = min(99.0, (connection.backfill_snapshots_created / estimated_total) * 100)
        elif connection.backfill_status == BackfillStatus.completed:
            progress_percent = 100.0
        elif connection.backfill_status == BackfillStatus.failed:
            progress_percent = 0.0

        return {
            "status": connection.backfill_status.value,
            "progress_percent": progress_percent,
            "snapshots_created": connection.backfill_snapshots_created,
            "error_message": connection.backfill_error,
            "started_at": connection.backfill_started_at.isoformat() + "Z" if connection.backfill_started_at else None,
            "completed_at": connection.backfill_completed_at.isoformat() + "Z" if connection.backfill_completed_at else None,
        }


def trigger_backfill_job(connection_id: UUID) -> None:
    """Trigger a background backfill job for a connection.

    This function is called after a successful Plaid sync to schedule
    the backfill to run asynchronously.

    Args:
        connection_id: ID of the PlaidConnection to backfill
    """
    from app.scheduler import scheduler

    # Add a one-time job to run the backfill
    scheduler.add_job(
        run_backfill_job,
        trigger="date",
        args=[connection_id],
        id=f"backfill_{connection_id}",
        name=f"Backfill snapshots for connection {connection_id}",
        replace_existing=True,
    )
    logger.info("Scheduled backfill job for connection %s", connection_id)


async def run_backfill_job(connection_id: UUID) -> None:
    """Background job to run the backfill.

    Args:
        connection_id: ID of the PlaidConnection to backfill
    """
    from app.db.database import SessionLocal

    logger.info("Starting backfill job for connection %s", connection_id)

    db = SessionLocal()
    try:
        service = HistoricalBackfillService(db)
        result = service.backfill_connection(connection_id)
        logger.info(
            "Backfill job completed for connection %s: %s, %d snapshots",
            connection_id,
            result.status,
            result.snapshots_created,
        )
    except Exception as e:
        logger.exception("Backfill job failed for connection %s: %s", connection_id, e)
    finally:
        db.close()
