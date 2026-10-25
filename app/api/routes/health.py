"""Health and introspection endpoints (unauthenticated)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..deps import get_runtime

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health(request: Request, deep: bool = Query(default=False)) -> dict:
    """Liveness plus readiness of ASR, TTS and (optionally) every HA instance."""
    runtime = get_runtime(request)
    instances: dict[str, object] = {}
    for name, client in runtime.ha.items():
        instances[name] = await client.health() if deep else "unknown"
    return {
        "status": "ok",
        "commands": len(runtime.commands.commands),
        "default_instance": runtime.default_instance,
        "asr": {"ready": True, "model": "bg-streaming"},
        "tts": {
            "ready": runtime.tts.ready,
            "reason": runtime.tts.unavailable_reason,
        },
        "instances": instances,
    }
