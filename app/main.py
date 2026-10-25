"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from .api.deps import require_api_key
from .api.routes import admin, health, voice
from .config import Settings
from .runtime import RuntimeBuilder, build_runtime

log = logging.getLogger(__name__)


def create_app(
    *,
    builder: RuntimeBuilder = build_runtime,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the app. ``builder``/``settings`` are injectable for tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        current = settings or Settings()
        logging.basicConfig(
            level=getattr(logging, current.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        app.state.settings = current
        app.state.builder = builder
        app.state.runtime = await builder(current)
        app.state.audio_cache = OrderedDict()
        try:
            yield
        finally:
            await app.state.runtime.aclose()

    app = FastAPI(
        title="Hass BG Gateway",
        description=(
            "Bulgarian speech -> configured command -> one Home Assistant. "
            "Actions reply with text; commands marked speak:true also return audio. "
            "Every /v1 endpoint accepts an optional X-API-Key."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.include_router(health.router, dependencies=[Depends(require_api_key)])
    app.include_router(voice.router, dependencies=[Depends(require_api_key)])
    app.include_router(admin.router, dependencies=[Depends(require_api_key)])

    @app.get("/", include_in_schema=False)
    async def index() -> dict:
        return {"service": "hass-bg-gateway", "docs": "/docs"}

    return app


app = create_app()
