from __future__ import annotations

import io
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import CommandsFile, InstancesFile, Settings
from app.main import create_app
from app.matcher import Matcher
from app.runtime import Runtime

COMMANDS = {
    "language": "bg",
    "match": {"threshold": 88, "margin": 5},
    "commands": {
        "HallLightOff": {
            "speak": False,
            "phrases": ["изключи лампата в хола", "изгаси лампата в хола"],
        },
        "OutsideTemperature": {
            "speak": True,
            "phrases": ["каква е температурата отвън"],
        },
    },
}


def wav_bytes(seconds: float = 0.5, rate: int = 16_000) -> bytes:
    samples = np.zeros(int(seconds * rate), dtype=np.float32)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((samples * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


class FakeAsr:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, samples, *, chunk: int = 1600) -> str:
        self.calls += 1
        return self.text


class FakeTts:
    def __init__(self, ready: bool = True) -> None:
        self._ready = ready

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def unavailable_reason(self) -> str | None:
        return None if self._ready else "test disabled"

    def synthesize(self, text: str) -> bytes | None:
        return wav_bytes() if self._ready else None


class FakeHa:
    def __init__(self, text: str = "Изключих лампата в хола.") -> None:
        self.text = text
        self.intents: list[str] = []

    async def handle_intent(self, intent: str) -> dict:
        self.intents.append(intent)
        return {"text": self.text, "response_type": "action_done", "data": {}}

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def build_runtime(
    *,
    transcript: str = "изключи лампата в хола",
    ha_text: str = "Изключих лампата в хола.",
    tts_ready: bool = True,
) -> Runtime:
    commands = CommandsFile.model_validate(COMMANDS)
    instances = InstancesFile.model_validate(
        {
            "default": "home",
            "instances": {"home": {"url": "http://ha.local:8123", "token": "test"}},
        }
    )
    return Runtime(
        settings=Settings(api_key=None),
        commands=commands,
        instances=instances,
        matcher=Matcher(commands.commands),
        asr=FakeAsr(transcript),
        tts=FakeTts(tts_ready),
        ha={"home": FakeHa(ha_text)},
        default_instance="home",
    )


def client_for(runtime: Runtime, *, api_key: str | None = None) -> TestClient:
    settings = Settings(api_key=api_key)

    async def builder(_: Settings) -> Runtime:
        return runtime

    return TestClient(create_app(builder=builder, settings=settings))


def test_action_returns_text_only() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post("/v1/command", json={"text": "изключи лампата в хола"})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "action"
    assert body["speak"] is False
    assert body["audio_url"] is None
    assert body["matched"]["intent"] == "HallLightOff"
    assert body["text"] == "Изключих лампата в хола."
    assert runtime.ha["home"].intents == ["HallLightOff"]


def test_speak_command_returns_audio() -> None:
    runtime = build_runtime(ha_text="В момента температурата навън е 11 градуса.")
    with client_for(runtime) as client:
        response = client.post(
            "/v1/command", json={"text": "каква е температурата отвън"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "query"
        assert body["speak"] is True
        assert body["audio_url"]

        audio = client.get(body["audio_url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.content[:4] == b"RIFF"


def test_speak_can_be_overridden_off() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post(
            "/v1/command",
            json={"text": "каква е температурата отвън", "speak": "false"},
        )
    body = response.json()
    assert body["kind"] == "query"
    assert body["speak"] is False
    assert body["audio_url"] is None


def test_no_match_does_not_call_home_assistant() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post("/v1/command", json={"text": "пусни музика"})
    body = response.json()
    assert response.status_code == 200
    assert body["kind"] == "no_match"
    assert body["text"] == "Не разбрах. Повторете, моля."
    assert runtime.ha["home"].intents == []


def test_voice_upload_runs_asr_and_action() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post(
            "/v1/voice",
            files={"audio": ("command.wav", wav_bytes(), "audio/wav")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "изключи лампата в хола"
    assert body["matched"]["intent"] == "HallLightOff"
    assert runtime.asr.calls == 1


def test_voice_raw_accepts_body() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post(
            "/v1/voice/raw",
            content=wav_bytes(),
            headers={"Content-Type": "audio/wav"},
        )
    assert response.status_code == 200
    assert response.json()["kind"] == "action"


def test_rejects_wrong_audio_format() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post(
            "/v1/voice",
            files={"audio": ("bad.wav", wav_bytes(rate=8_000), "audio/wav")},
        )
    assert response.status_code == 422


def test_api_key_is_enforced() -> None:
    runtime = build_runtime()
    with client_for(runtime, api_key="secret") as client:
        assert client.post("/v1/command", json={"text": "изключи лампата в хола"}).status_code == 401
        ok = client.post(
            "/v1/command",
            json={"text": "изключи лампата в хола"},
            headers={"X-API-Key": "secret"},
        )
    assert ok.status_code == 200


def test_health_and_commands() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        health = client.get("/v1/health?deep=true").json()
        commands = client.get("/v1/commands").json()
    assert health["status"] == "ok"
    assert health["asr"]["ready"] is True
    assert health["instances"]["home"] is True
    assert commands["language"] == "bg"
    names = {entry["intent"] for entry in commands["commands"]}
    assert names == {"HallLightOff", "OutsideTemperature"}


def test_unknown_instance_is_404() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post(
            "/v1/command", json={"text": "изключи лампата в хола", "instance": "nope"}
        )
    assert response.status_code == 404
