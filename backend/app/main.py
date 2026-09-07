"""Minimal FastAPI application entry point."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Callable
import logging

from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import router as api_router
from app.core.config import get_settings
from app.core.exceptions import ResearchError, USBError
from app.core.logging import configure_logging
from app.execution.config import ExecutionConfig
from app.market import factory as market_factory
from app.services.position_management_runtime import (
    start_position_management_runtime, stop_position_management_runtime,
)
from app.services.simulation_runtime import (
    activate_operator_simulation_runtime, clear_active_sim_broker,
)


settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("Application starting")
    # Read at startup rather than reusing the import-time instance so the process
    # activates against the profile it was actually launched with.
    current = get_settings()
    if current.market_data_provider == "kiwoom":
        logger.warning("KIWOOM: %s MARKET DATA / ORDERING DISABLED", current.kiwoom_env.upper())
        logger.warning("BROKER: SIMULATION")
    # Config is injected, never read back from the database: persisted state carries
    # figures, not the execution assumptions that produced them.
    runtime = activate_operator_simulation_runtime(current, config=ExecutionConfig())
    try:
        if runtime is not None:
            # Provider construction is lazy inside the owner: an empty book performs
            # neither Kiwoom authentication nor minute-bar requests. The factory is
            # reached through its module, not through a name bound here, so the
            # composition seam the suite already patches covers this call site too.
            provider_factory = getattr(
                _app.state, "position_market_data_provider_factory",
                lambda: market_factory.build_kiwoom_provider(current),
            )
            start_position_management_runtime(runtime, provider_factory)
        yield
    finally:
        # Ownership is released without saving; every durable figure was already
        # committed by the execution transaction that produced it. It is released
        # whatever the cadence owner did, because a task that failed to stop
        # cleanly must not leave the process holding a broker nobody can replace.
        try:
            await stop_position_management_runtime()
        finally:
            clear_active_sim_broker()
            logger.info("Application stopping")


def error_response(status: int, code: str, message: str, details: object | None = None) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


def create_app(*, position_market_data_provider_factory: Callable | None = None) -> FastAPI:
    application = FastAPI(title=settings.app_name, version="1.0", lifespan=lifespan)
    if position_market_data_provider_factory is not None:
        application.state.position_market_data_provider_factory = position_market_data_provider_factory
    application.add_middleware(CORSMiddleware, allow_origins=settings.allowed_cors_origins,
                               allow_credentials=True, allow_methods=["GET", "POST", "PUT"],
                               allow_headers=["Content-Type", "X-Request-ID"])

    @application.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "REQUEST_VALIDATION_ERROR", "Request validation failed", exc.errors())

    @application.exception_handler(LookupError)
    async def not_found_handler(_request: Request, exc: LookupError) -> JSONResponse:
        return error_response(404, "RESOURCE_NOT_FOUND", str(exc))

    @application.exception_handler(ResearchError)
    async def research_handler(_request: Request, exc: ResearchError) -> JSONResponse:
        message = str(exc); lowered = message.lower()
        status = 409 if any(word in lowered for word in ("already", "at most", "duplicate")) else 400
        code = "RESEARCH_CONFLICT" if status == 409 else "RESEARCH_INVALID"
        return error_response(status, code, message)

    @application.exception_handler(ValueError)
    async def conflict_handler(_request: Request, exc: ValueError) -> JSONResponse:
        message = str(exc); status = 400 if "confirmation" in message or "must be on or before" in message else 409
        return error_response(status, "INVALID_REQUEST" if status == 400 else "STATE_CONFLICT", message)

    @application.exception_handler(USBError)
    async def domain_handler(_request: Request, exc: USBError) -> JSONResponse:
        return error_response(409, "DOMAIN_CONFLICT", str(exc))

    @application.exception_handler(Exception)
    async def internal_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API exception", exc_info=exc)
        return error_response(500, "INTERNAL_SERVER_ERROR", "Unexpected server error")

    application.include_router(api_router)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name}
    return application


app = create_app()
