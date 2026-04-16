"""Unit tests for historical backfill service.

Tests the core reconstruction algorithm and price extraction/fill logic.
"""

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import Holding, Transaction, TransactionType
from app.plaid.historical_backfill import (
    HistoricalBackfillService,
    ReconstructedState,
)


class TestReconstructHoldingAtDate:
    """Tests for the reconstruct_holding_at_date method."""

    def test_no_transactions_returns_current_state(self):
        """With no transactions, should return current holding quantity."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 100.0
        holding.cost_basis_total = 5000.0

        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[],
            target_date=date.today() - timedelta(days=30),
            price=50.0,
        )

        assert result is not None
        assert result.quantity == 100.0
        assert result.price == 50.0
        assert result.value == 5000.0

    def test_reverse_buy_transaction(self):
        """Reversing a buy should reduce quantity."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 100.0
        holding.cost_basis_total = 5000.0

        # Transaction: bought 20 shares 10 days ago
        txn = MagicMock(spec=Transaction)
        txn.transaction_date = date.today() - timedelta(days=10)
        txn.transaction_type = TransactionType.buy
        txn.quantity = 20.0

        # Target date: 15 days ago (before the buy)
        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[txn],
            target_date=date.today() - timedelta(days=15),
            price=50.0,
        )

        assert result is not None
        assert result.quantity == 80.0  # 100 - 20
        assert result.value == 4000.0

    def test_reverse_sell_transaction(self):
        """Reversing a sell should increase quantity."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 80.0
        holding.cost_basis_total = 4000.0

        # Transaction: sold 20 shares 10 days ago
        txn = MagicMock(spec=Transaction)
        txn.transaction_date = date.today() - timedelta(days=10)
        txn.transaction_type = TransactionType.sell
        txn.quantity = 20.0

        # Target date: 15 days ago (before the sell)
        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[txn],
            target_date=date.today() - timedelta(days=15),
            price=50.0,
        )

        assert result is not None
        assert result.quantity == 100.0  # 80 + 20
        assert result.value == 5000.0

    def test_reverse_transfer_in(self):
        """Reversing a transfer_in should reduce quantity."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 100.0
        holding.cost_basis_total = 5000.0

        txn = MagicMock(spec=Transaction)
        txn.transaction_date = date.today() - timedelta(days=10)
        txn.transaction_type = TransactionType.transfer_in
        txn.quantity = 30.0

        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[txn],
            target_date=date.today() - timedelta(days=15),
            price=50.0,
        )

        assert result is not None
        assert result.quantity == 70.0  # 100 - 30

    def test_reverse_transfer_out(self):
        """Reversing a transfer_out should increase quantity."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 70.0
        holding.cost_basis_total = 3500.0

        txn = MagicMock(spec=Transaction)
        txn.transaction_date = date.today() - timedelta(days=10)
        txn.transaction_type = TransactionType.transfer_out
        txn.quantity = 30.0

        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[txn],
            target_date=date.today() - timedelta(days=15),
            price=50.0,
        )

        assert result is not None
        assert result.quantity == 100.0  # 70 + 30

    def test_negative_quantity_returns_none(self):
        """If reconstructed quantity goes negative, holding didn't exist."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 50.0
        holding.cost_basis_total = 2500.0

        # Transaction: bought 100 shares 10 days ago
        txn = MagicMock(spec=Transaction)
        txn.transaction_date = date.today() - timedelta(days=10)
        txn.transaction_type = TransactionType.buy
        txn.quantity = 100.0

        # Target date: before the buy - would result in -50 shares
        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[txn],
            target_date=date.today() - timedelta(days=15),
            price=50.0,
        )

        assert result is None

    def test_zero_quantity_returns_none(self):
        """If reconstructed quantity is zero, return None."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 50.0
        holding.cost_basis_total = 2500.0

        # Transaction: bought exactly 50 shares
        txn = MagicMock(spec=Transaction)
        txn.transaction_date = date.today() - timedelta(days=10)
        txn.transaction_type = TransactionType.buy
        txn.quantity = 50.0

        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[txn],
            target_date=date.today() - timedelta(days=15),
            price=50.0,
        )

        assert result is None

    def test_no_price_returns_none(self):
        """If no price available, return None."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 100.0
        holding.cost_basis_total = 5000.0

        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[],
            target_date=date.today() - timedelta(days=15),
            price=None,
        )

        assert result is None

    def test_multiple_transactions(self):
        """Test with multiple transactions."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 100.0
        holding.cost_basis_total = 5000.0

        # Transactions (ordered by date desc as they would be from DB)
        transactions = [
            # Most recent: sold 10 shares 5 days ago
            MagicMock(
                transaction_date=date.today() - timedelta(days=5),
                transaction_type=TransactionType.sell,
                quantity=10.0,
            ),
            # Earlier: bought 30 shares 15 days ago
            MagicMock(
                transaction_date=date.today() - timedelta(days=15),
                transaction_type=TransactionType.buy,
                quantity=30.0,
            ),
            # Earliest: bought 50 shares 30 days ago
            MagicMock(
                transaction_date=date.today() - timedelta(days=30),
                transaction_type=TransactionType.buy,
                quantity=50.0,
            ),
        ]

        # Target date: 20 days ago (after 30-day buy, before 15-day buy)
        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=transactions,
            target_date=date.today() - timedelta(days=20),
            price=50.0,
        )

        # Start: 100
        # Reverse sell (+10): 110
        # Reverse 15-day buy (-30): 80
        # Don't reverse 30-day buy (before target)
        assert result is not None
        assert result.quantity == 80.0

    def test_transaction_on_target_date_included(self):
        """Transactions ON the target date should be included in final state."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        holding = MagicMock(spec=Holding)
        holding.id = uuid.uuid4()
        holding.security_id = uuid.uuid4()
        holding.quantity = 100.0
        holding.cost_basis_total = 5000.0

        target_date = date.today() - timedelta(days=10)

        # Transaction ON the target date
        txn = MagicMock(spec=Transaction)
        txn.transaction_date = target_date
        txn.transaction_type = TransactionType.buy
        txn.quantity = 20.0

        result = service.reconstruct_holding_at_date(
            holding=holding,
            transactions=[txn],
            target_date=target_date,
            price=50.0,
        )

        # Transaction on target date should NOT be reversed
        assert result is not None
        assert result.quantity == 100.0


class TestExtractPricesFromTransactions:
    """Tests for price extraction from transactions."""

    def test_extracts_prices_from_transactions(self):
        """Should extract prices from transactions with price_per_unit."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        security_id = uuid.uuid4()
        transactions = [
            MagicMock(
                transaction_date=date.today() - timedelta(days=5),
                price_per_unit=52.0,
            ),
            MagicMock(
                transaction_date=date.today() - timedelta(days=10),
                price_per_unit=48.0,
            ),
            MagicMock(
                transaction_date=date.today() - timedelta(days=15),
                price_per_unit=50.0,
            ),
        ]

        prices = service.extract_prices_from_transactions(security_id, transactions)

        assert len(prices) == 3
        assert prices[date.today() - timedelta(days=5)] == 52.0
        assert prices[date.today() - timedelta(days=10)] == 48.0
        assert prices[date.today() - timedelta(days=15)] == 50.0

    def test_skips_null_prices(self):
        """Should skip transactions without prices."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        transactions = [
            MagicMock(
                transaction_date=date.today() - timedelta(days=5),
                price_per_unit=52.0,
            ),
            MagicMock(
                transaction_date=date.today() - timedelta(days=10),
                price_per_unit=None,
            ),
            MagicMock(
                transaction_date=date.today() - timedelta(days=15),
                price_per_unit=0,  # Zero should be skipped too
            ),
        ]

        prices = service.extract_prices_from_transactions(uuid.uuid4(), transactions)

        assert len(prices) == 1
        assert prices[date.today() - timedelta(days=5)] == 52.0


class TestForwardFillPrices:
    """Tests for forward-fill price gap logic."""

    def test_forward_fill_gaps(self):
        """Should fill gaps using last known price."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        start_date = date.today() - timedelta(days=5)
        end_date = date.today()

        # Only have price for day 2
        prices = {
            date.today() - timedelta(days=3): 50.0,
        }

        filled = service.forward_fill_prices(
            prices=prices,
            start_date=start_date,
            end_date=end_date,
            fallback_price=45.0,
        )

        # Days 5, 4 should use fallback (45.0)
        # Days 3, 2, 1, 0 should use 50.0
        assert filled[date.today() - timedelta(days=5)] == 45.0
        assert filled[date.today() - timedelta(days=4)] == 45.0
        assert filled[date.today() - timedelta(days=3)] == 50.0
        assert filled[date.today() - timedelta(days=2)] == 50.0
        assert filled[date.today() - timedelta(days=1)] == 50.0
        assert filled[date.today()] == 50.0

    def test_forward_fill_with_multiple_known_prices(self):
        """Should update to new price when encountered."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        start_date = date.today() - timedelta(days=6)
        end_date = date.today()

        prices = {
            date.today() - timedelta(days=5): 48.0,
            date.today() - timedelta(days=2): 52.0,
        }

        filled = service.forward_fill_prices(
            prices=prices,
            start_date=start_date,
            end_date=end_date,
            fallback_price=45.0,
        )

        assert filled[date.today() - timedelta(days=6)] == 45.0
        assert filled[date.today() - timedelta(days=5)] == 48.0
        assert filled[date.today() - timedelta(days=4)] == 48.0
        assert filled[date.today() - timedelta(days=3)] == 48.0
        assert filled[date.today() - timedelta(days=2)] == 52.0
        assert filled[date.today() - timedelta(days=1)] == 52.0
        assert filled[date.today()] == 52.0

    def test_forward_fill_no_data(self):
        """With no prices and no fallback, should return empty dict."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        filled = service.forward_fill_prices(
            prices={},
            start_date=date.today() - timedelta(days=5),
            end_date=date.today(),
            fallback_price=None,
        )

        assert filled == {}

    def test_forward_fill_all_days_have_prices(self):
        """When all days have prices, should use those."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        start_date = date.today() - timedelta(days=2)
        end_date = date.today()

        prices = {
            date.today() - timedelta(days=2): 48.0,
            date.today() - timedelta(days=1): 50.0,
            date.today(): 52.0,
        }

        filled = service.forward_fill_prices(
            prices=prices,
            start_date=start_date,
            end_date=end_date,
            fallback_price=45.0,
        )

        assert filled[date.today() - timedelta(days=2)] == 48.0
        assert filled[date.today() - timedelta(days=1)] == 50.0
        assert filled[date.today()] == 52.0


class TestReconstructedState:
    """Tests for the ReconstructedState dataclass."""

    def test_dataclass_creation(self):
        """Should create ReconstructedState with all fields."""
        state = ReconstructedState(
            holding_id=uuid.uuid4(),
            security_id=uuid.uuid4(),
            target_date=date.today(),
            quantity=100.0,
            price=50.0,
            value=5000.0,
            cost_basis_total=4500.0,
        )

        assert state.quantity == 100.0
        assert state.price == 50.0
        assert state.value == 5000.0
        assert state.cost_basis_total == 4500.0

    def test_dataclass_with_none_cost_basis(self):
        """Should allow None cost_basis_total."""
        state = ReconstructedState(
            holding_id=uuid.uuid4(),
            security_id=uuid.uuid4(),
            target_date=date.today(),
            quantity=100.0,
            price=50.0,
            value=5000.0,
            cost_basis_total=None,
        )

        assert state.cost_basis_total is None


class TestBackfillStatusCalculation:
    """Tests for backfill status calculation."""

    def test_pending_status(self):
        """Pending status should show 0% progress."""
        from app.db.models import BackfillStatus

        db = MagicMock()
        service = HistoricalBackfillService(db)

        connection = MagicMock()
        connection.backfill_status = BackfillStatus.pending
        connection.backfill_snapshots_created = 0
        connection.backfill_error = None
        connection.backfill_started_at = None
        connection.backfill_completed_at = None

        db.query.return_value.filter.return_value.first.return_value = connection

        result = service.get_backfill_status(uuid.uuid4())

        assert result["status"] == "pending"
        assert result["progress_percent"] == 0.0

    def test_completed_status(self):
        """Completed status should show 100% progress."""
        from app.db.models import BackfillStatus

        db = MagicMock()
        service = HistoricalBackfillService(db)

        connection = MagicMock()
        connection.backfill_status = BackfillStatus.completed
        connection.backfill_snapshots_created = 14600
        connection.backfill_error = None
        connection.backfill_started_at = None
        connection.backfill_completed_at = None

        db.query.return_value.filter.return_value.first.return_value = connection

        result = service.get_backfill_status(uuid.uuid4())

        assert result["status"] == "completed"
        assert result["progress_percent"] == 100.0

    def test_not_found_connection(self):
        """Non-existent connection should return not_found status."""
        db = MagicMock()
        service = HistoricalBackfillService(db)

        db.query.return_value.filter.return_value.first.return_value = None

        result = service.get_backfill_status(uuid.uuid4())

        assert result["status"] == "not_found"
        assert result["error_message"] == "Connection not found"
