"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, requests, votes
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.db.pool import close_pool, init_pool
from app.services import analyses as analyses_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = await init_pool(settings)
    # Analyses left pending by a previous process are failed as INTERRUPTED so
    # they are visibly retryable instead of pending forever.
    await analyses_service.release_interrupted_analyses(pool, settings=settings)
    try:
        yield
    finally:
        await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Signal API",
        description="Feature Intelligence System — turns feature requests into reviewable product decisions.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        # X-Voter-Key carries the browser-scoped demo identity for voting.
        allow_headers=["Content-Type", "X-Voter-Key"],
    )

    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(requests.router)
    app.include_router(votes.router)
    return app


app = create_app()
