import plaid
from plaid.api import plaid_api

from app.core.config import settings
from app.core.exceptions import AppException
from starlette import status


def get_plaid_host():
    env = (settings.plaid_env or "sandbox").lower().strip()
    # Note: Plaid SDK v7+ uses Sandbox for development. There is no separate "Development" environment.
    if env in ("sandbox", "development", "dev"):
        return plaid.Environment.Sandbox
    if env in ("production", "prod"):
        return plaid.Environment.Production
    raise AppException(
        code="CONFIG_ERROR",
        message="PLAID_ENV must be one of: sandbox, development, production.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def get_plaid_client() -> plaid_api.PlaidApi:
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise AppException(
            code="CONFIG_ERROR",
            message="PLAID_CLIENT_ID and PLAID_SECRET must be set in the environment.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    configuration = plaid.Configuration(
        host=get_plaid_host(),
        api_key={
            "clientId": settings.plaid_client_id,
            "secret": settings.plaid_secret,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)
