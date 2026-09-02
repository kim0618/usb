"""Minimal FastAPI application entry point."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
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


settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("Application starting")
    yield
    logger.info("Application stopping")


def error_response(status: int, code: str, message: str, details: object | None = None) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name, version="1.0", lifespan=lifespan)
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
        message = str(exc); status = 400 if "confirmation" in message else 409
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
