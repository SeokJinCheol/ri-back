from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
    )
    application.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-User-Email", "X-User-Name"],
    )
    application.include_router(api_router, prefix="/api/v1")

    @application.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {"message": settings.app_name, "docs": "/docs"}

    return application


app = create_app()
