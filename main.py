from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import AppException
from app.logging_config import RequestLoggingMiddleware, configure_logging
from app.routers.deal_router import router as deal_router

from app.services.config import settings
configure_logging(level=settings.LOG_LEVEL)

app = FastAPI(title="Deal Service", version="1.0.0")
app.add_middleware(RequestLoggingMiddleware)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"message": "An unexpected internal server error occurred."},
    )


app.include_router(deal_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
