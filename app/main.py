import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Configure logging to show INFO level messages
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
from fastapi.middleware.cors import CORSMiddleware

from app.allocation.routes import router as allocation_router
from app.auth.routes import router as auth_router
from app.chat.routes import router as chat_router
from app.core.exceptions import register_exception_handlers
from app.core.responses import success_response
from app.middleware.auth import JWTAuthMiddleware
from app.middleware.logging import RequestLoggingMiddleware
from app.plaid.routes import router as plaid_router
from app.portfolio.routes import router as portfolio_router
from app.scheduler import shutdown_scheduler, start_scheduler
from app.tax.routes import router as tax_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    start_scheduler()
    yield
    # Shutdown
    shutdown_scheduler()


app = FastAPI(
    lifespan=lifespan,
    title="Quantly Backend",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=[
        {"name": "auth", "description": "Signup, login, JWT refresh, password reset."},
        {
            "name": "plaid",
            "description": (
                "Plaid Link: link-token and public_token exchange. **Requires** a valid "
                "`Authorization: Bearer <access_token>` header — click **Authorize** in Swagger."
            ),
        },
        {"name": "portfolio", "description": "Portfolio (placeholder)."},
        {"name": "allocation", "description": "Allocation (placeholder)."},
        {"name": "tax", "description": "Tax (placeholder)."},
        {"name": "chat", "description": "Chat (placeholder)."},
    ],
)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(JWTAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/v1")
app.include_router(plaid_router, prefix="/v1")
app.include_router(portfolio_router, prefix="/v1")
app.include_router(allocation_router, prefix="/v1")
app.include_router(tax_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")

register_exception_handlers(app)


@app.get("/")
def read_root():
    body, _ = success_response({"message": "Backend is running"})
    return body
