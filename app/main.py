"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections import OrderedDict
from contextlib import asynccontextmanager

from dotenv import load_dotenv
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
        # .env holds HA_*_TOKEN values that instances.yaml references by name;
        # pydantic-settings reads BGV_* from .env but does not export it.
        load_dotenv(override=False)
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
        title="HA Bulgarian Commands",
        description=(
            "Bulgarian speech -> configured command -> Home Assistant intent. "
            "Actions reply with text; commands marked speak:true also return audio."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(voice.router, dependencies=[Depends(require_api_key)])
    app.include_router(admin.router, dependencies=[Depends(require_api_key)])

    @app.get("/", include_in_schema=False)
    async def index() -> dict:
        return {"service": "hass-bulgarian-commands", "docs": "/docs"}

    return app


app = create_app()
