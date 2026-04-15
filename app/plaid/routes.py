from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.core.responses import success_response
from app.db.database import SessionLocal
from app.db.models import User
from app.plaid.schemas import PlaidExchangeRequest, PlaidWebhookRequest

router = APIRouter(prefix="/plaid", tags=["plaid"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/link-token",
    summary="Create Plaid Link token",
    description=(
        "Creates a `link_token` for Plaid Link. Use **Authorize** and paste the `access_token` "
        "from `POST /v1/auth/login`."
    ),
)
def create_link_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).create_link_token(user_id=current_user.id)
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.post(
    "/exchange",
    status_code=201,
    summary="Exchange Plaid public token",
    description=(
        "Call after Plaid Link `onSuccess` with the `public_token`. Persists the Item for the "
        "authenticated user. Requires Bearer auth."
    ),
)
def exchange_public_token(
    payload: PlaidExchangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).exchange_public_token(
        user_id=current_user.id,
        public_token=payload.public_token,
        institution_id=payload.institution_id,
        institution_name=payload.institution_name,
    )
    body, status_code = success_response(data, status_code=201)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/connections",
    summary="List Plaid connections",
    description="Returns linked institutions for the current user (no access tokens).",
)
def list_plaid_connections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).list_connections(user_id=current_user.id)
    body, status_code = success_response({"connections": data})
    return JSONResponse(content=body, status_code=status_code)


@router.delete(
    "/connections/{connection_id}",
    summary="Disconnect Plaid institution",
    description="Revokes the Item at Plaid and removes the stored connection.",
)
def disconnect_plaid_connection(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).disconnect_connection(
        user_id=current_user.id,
        connection_id=connection_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/connections/{connection_id}/investments",
    summary="Get investments from Plaid",
    description="Fetches accounts, holdings, and securities from Plaid for the specified connection.",
)
def get_investments(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_investments(
        user_id=current_user.id,
        connection_id=connection_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.post(
    "/connections/{connection_id}/sync",
    summary="Sync investments from Plaid",
    description="Fetches and stores investments data from Plaid. Use this for manual sync or scheduled refreshes.",
)
def sync_investments(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).ingest_investments(
        user_id=current_user.id,
        connection_id=connection_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/connections/{connection_id}/portfolio",
    summary="Get stored portfolio data",
    description="Returns accounts, holdings, and securities stored in the database for this connection.",
)
def get_portfolio(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_stored_portfolio(
        user_id=current_user.id,
        connection_id=connection_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/connections/{connection_id}/sync-status",
    summary="Get sync status",
    description="Returns the current sync status for this connection.",
)
def get_sync_status(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_sync_status(
        user_id=current_user.id,
        connection_id=connection_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/accounts",
    summary="Get all accounts",
    description="Returns all investment accounts for the current user.",
)
def get_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_accounts(user_id=current_user.id)
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/holdings",
    summary="Get all holdings",
    description="Returns all holdings for the current user with security details.",
)
def get_holdings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_holdings(user_id=current_user.id)
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/holdings/{holding_id}/history",
    summary="Get holding performance history",
    description="Returns performance snapshots for a specific holding over time. Use for timeline/chart views.",
)
def get_holding_history(
    holding_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    start_date: Optional[date] = Query(default=None, description="Start of date range (inclusive)"),
    end_date: Optional[date] = Query(default=None, description="End of date range (inclusive)"),
    limit: int = Query(default=365, ge=1, le=1000, description="Maximum number of snapshots to return"),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_holding_history(
        user_id=current_user.id,
        holding_id=holding_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/transactions",
    summary="Get all transactions",
    description="Returns investment transactions for the current user with pagination. Use 'page' for page number (0-indexed).",
)
def get_transactions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    page: int = Query(default=0, ge=0),
    account_id: Optional[UUID] = Query(default=None),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_transactions(
        user_id=current_user.id,
        limit=limit,
        page=page,
        account_id=account_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.post(
    "/webhook",
    summary="Plaid webhook endpoint",
    description="Receives webhooks from Plaid for transaction updates. No authentication required.",
)
def handle_webhook(
    payload: PlaidWebhookRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).handle_webhook(
        webhook_type=payload.webhook_type,
        webhook_code=payload.webhook_code,
        item_id=payload.item_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.post(
    "/connections/{connection_id}/sync-transactions",
    summary="Sync banking transactions",
    description="Manually trigger a sync of banking transactions using Plaid's /transactions/sync endpoint.",
)
def sync_transactions(
    connection_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).sync_transactions(
        user_id=current_user.id,
        connection_id=connection_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get(
    "/banking-transactions",
    summary="Get banking transactions",
    description="Returns banking transactions for the current user with pagination. Use 'page' for page number (0-indexed).",
)
def get_banking_transactions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    page: int = Query(default=0, ge=0),
    account_id: Optional[UUID] = Query(default=None),
) -> JSONResponse:
    from app.plaid.service import PlaidService

    data = PlaidService(db).get_banking_transactions(
        user_id=current_user.id,
        limit=limit,
        page=page,
        account_id=account_id,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)
