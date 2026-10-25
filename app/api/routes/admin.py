"""Admin endpoints: reload configuration without restarting the process."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..deps import get_runtime

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["admin"])


@router.post("/reload")
async def reload(request: Request) -> dict:
    """Re-read commands.yaml + instances.yaml and rebuild the runtime.

    Rebuilding reloads the ASR model (~1 s) so that hotword changes take
    effect. In-flight requests finish on the old runtime.
    """
    runtime = get_runtime(request)
    builder = request.app.state.builder
    settings = request.app.state.settings

    new_runtime = await builder(settings)
    request.app.state.runtime = new_runtime
    await runtime.aclose()

    log.info("configuration reloaded: %d commands", len(new_runtime.commands.commands))
    return {
        "status": "reloaded",
        "commands": len(new_runtime.commands.commands),
        "instances": sorted(new_runtime.ha),
    }
