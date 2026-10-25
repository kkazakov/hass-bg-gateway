"""Health and introspection endpoints (behind the API key when configured)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..deps import get_runtime

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health(request: Request, deep: bool = Query(default=False)) -> dict:
    """Liveness plus readiness of ASR, TTS and the Home Assistant connection."""
    runtime = get_runtime(request)
    settings = request.app.state.settings
    return {
        "status": "ok",
        "commands": len(runtime.commands.commands),
        "asr": {"ready": True, "model": "bg-streaming"},
        "tts": {
            "ready": runtime.tts.ready,
            "provider": runtime.tts.provider,
            "voice": runtime.tts.voice,
            "reason": runtime.tts.unavailable_reason,
        },
        "ha": {
            "url": settings.ha_url,
            "ready": await runtime.ha.health() if deep else "unknown",
        },
        "auth": {"api_key_required": bool(settings.api_key)},
    }
