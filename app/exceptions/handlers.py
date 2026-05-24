import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .custom import AppException

logger = logging.getLogger("deal_service")


class GlobalExceptionHandlers:
    @staticmethod
    def register(app: FastAPI) -> None:
        @app.exception_handler(AppException)
        async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.error, "message": exc.message, "details": exc.details},
            )

        @app.exception_handler(Exception)
        async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
            logger.error("unhandled exception", exc_info=exc)
            return JSONResponse(
                status_code=500,
                content={"message": "An unexpected internal server error occurred."},
            )
