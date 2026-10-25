"""Voice endpoints: audio or text in, matched intent -> Home Assistant -> reply."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...audio import AudioError, decode_wav, pad
from ...ha import HaError
from ...runtime import Runtime
from ..deps import get_runtime

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["voice"])

MAX_AUDIO_BYTES = 2 * 1024 * 1024
AUDIO_CACHE_SIZE = 64

NO_MATCH_TEXT = "Не разбрах. Повторете, моля."


class Matched(BaseModel):
    intent: str
    phrase: str
    score: float
    runner_up: float


class Timings(BaseModel):
    asr_ms: float | None = None
    match_ms: float = 0.0
    ha_ms: float = 0.0
    tts_ms: float = 0.0
    total_ms: float = 0.0


class VoiceResponse(BaseModel):
    kind: Literal["action", "query", "no_match"]
    speak: bool
    text: str
    transcript: str
    audio_url: str | None = None
    instance: str
    matched: Matched | None = None
    timings: Timings
    reason: str | None = Field(default=None, description="why no match, for logs")


class CommandRequest(BaseModel):
    text: str
    instance: str | None = None
    speak: Literal["auto", "true", "false"] = "auto"


def _store_audio(request: Request, data: bytes) -> str:
    cache: OrderedDict[str, bytes] = request.app.state.audio_cache
    audio_id = uuid.uuid4().hex
    cache[audio_id] = data
    while len(cache) > AUDIO_CACHE_SIZE:
        cache.popitem(last=False)
    return f"/v1/audio/{audio_id}.wav"


async def _run_pipeline(
    request: Request,
    *,
    runtime: Runtime,
    transcript: str,
    instance: str | None,
    speak: Literal["auto", "true", "false"],
    timings: Timings,
    started: float,
) -> VoiceResponse:
    try:
        instance_name, client = runtime.client(instance)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    t0 = time.perf_counter()
    match = runtime.matcher.match(transcript)
    timings.match_ms = round((time.perf_counter() - t0) * 1000, 2)

    if not match.ok:
        log.info(
            "no_match instance=%s transcript=%r score=%.1f reason=%s",
            instance_name,
            transcript,
            match.score,
            match.reason,
        )
        timings.total_ms = round((time.perf_counter() - started) * 1000, 2)
        return VoiceResponse(
            kind="no_match",
            speak=False,
            text=NO_MATCH_TEXT,
            transcript=transcript,
            instance=instance_name,
            timings=timings,
            reason=match.reason,
        )

    command = runtime.command(match.intent)
    t0 = time.perf_counter()
    try:
        result = await client.handle_intent(match.intent)
    except HaError as exc:
        log.error("HA error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    timings.ha_ms = round((time.perf_counter() - t0) * 1000, 2)

    text = result["text"] or "Готово."
    should_speak = command.speak if speak == "auto" else speak == "true"

    audio_url = None
    if should_speak and runtime.tts.ready:
        t0 = time.perf_counter()
        try:
            audio = await asyncio.to_thread(runtime.tts.synthesize, text)
        except Exception as exc:  # TTS must never block the reply
            log.warning("TTS failed: %s", exc)
            audio = None
        timings.tts_ms = round((time.perf_counter() - t0) * 1000, 2)
        if audio:
            audio_url = _store_audio(request, audio)

    timings.total_ms = round((time.perf_counter() - started) * 1000, 2)
    log.info(
        "ok instance=%s intent=%s transcript=%r score=%.1f speak=%s total_ms=%.1f",
        instance_name,
        match.intent,
        transcript,
        match.score,
        should_speak,
        timings.total_ms,
    )
    return VoiceResponse(
        kind="query" if command.speak else "action",
        speak=should_speak,
        text=text,
        transcript=transcript,
        audio_url=audio_url,
        instance=instance_name,
        matched=Matched(
            intent=match.intent,
            phrase=match.phrase or "",
            score=round(match.score, 1),
            runner_up=round(match.runner_up, 1),
        ),
        timings=timings,
    )


@router.post("/voice", response_model=VoiceResponse)
async def voice(
    request: Request,
    audio: UploadFile,
    instance: str | None = Query(default=None),
    speak: Literal["auto", "true", "false"] = Query(default="auto"),
) -> VoiceResponse:
    """Upload a 16 kHz mono PCM16 WAV file and execute the configured command."""
    runtime = get_runtime(request)
    data = await audio.read()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"audio exceeds {MAX_AUDIO_BYTES} bytes",
        )
    try:
        samples = decode_wav(data)
    except AudioError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc)
        ) from exc

    padded = pad(
        samples,
        lead_s=runtime.settings.lead_silence_s,
        tail_s=runtime.settings.tail_silence_s,
    )
    timings = Timings()
    started = time.perf_counter()
    t0 = time.perf_counter()
    transcript = await asyncio.to_thread(runtime.asr.transcribe, padded)
    timings.asr_ms = round((time.perf_counter() - t0) * 1000, 2)

    return await _run_pipeline(
        request,
        runtime=runtime,
        transcript=transcript,
        instance=instance,
        speak=speak,
        timings=timings,
        started=started,
    )


@router.post("/voice/raw", response_model=VoiceResponse)
async def voice_raw(
    request: Request,
    instance: str | None = Query(default=None),
    speak: Literal["auto", "true", "false"] = Query(default="auto"),
) -> VoiceResponse:
    """Same as ``/v1/voice`` but takes the WAV as the raw request body (MCU friendly)."""
    runtime = get_runtime(request)
    data = await request.body()
    if not data:
        raise HTTPException(
            status_code=422, detail="empty body"
        )
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"audio exceeds {MAX_AUDIO_BYTES} bytes",
        )
    try:
        samples = decode_wav(data)
    except AudioError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc)
        ) from exc

    padded = pad(
        samples,
        lead_s=runtime.settings.lead_silence_s,
        tail_s=runtime.settings.tail_silence_s,
    )
    timings = Timings()
    started = time.perf_counter()
    t0 = time.perf_counter()
    transcript = await asyncio.to_thread(runtime.asr.transcribe, padded)
    timings.asr_ms = round((time.perf_counter() - t0) * 1000, 2)

    return await _run_pipeline(
        request,
        runtime=runtime,
        transcript=transcript,
        instance=instance,
        speak=speak,
        timings=timings,
        started=started,
    )


@router.post("/command", response_model=VoiceResponse)
async def command(request: Request, body: CommandRequest) -> VoiceResponse:
    """Text-in variant, for testing the mapping without audio."""
    runtime = get_runtime(request)
    return await _run_pipeline(
        request,
        runtime=runtime,
        transcript=body.text,
        instance=body.instance,
        speak=body.speak,
        timings=Timings(),
        started=time.perf_counter(),
    )


@router.get("/audio/{audio_id}.wav")
async def audio(audio_id: str, request: Request) -> Response:
    cache: OrderedDict[str, bytes] = request.app.state.audio_cache
    data = cache.get(audio_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="audio not found or expired"
        )
    return Response(content=data, media_type="audio/wav")


@router.get("/commands")
async def commands(request: Request) -> dict:
    runtime = get_runtime(request)
    entries = runtime.matcher.describe()
    for entry in entries:
        entry["speak"] = runtime.command(entry["intent"]).speak
    return {"language": runtime.commands.language, "commands": entries}
