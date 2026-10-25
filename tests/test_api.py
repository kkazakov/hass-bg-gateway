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
from app.tts import AudioClip

COMMANDS = {
    "language": "bg",
    "match": {"threshold": 88, "margin": 5},
    "commands": {
        "HallLightOff": {
            "speak": False,
            "phrases": ["изключи лампата в хола", "изгаси лампата в хола"],
            "action": {
                "service": "light.turn_off",
                "target": {"entity_id": "light.hall"},
                "response": "Изключих лампата в хола.",
            },
        },
        "OutsideTemperature": {
            "speak": True,
            "phrases": ["каква е температурата навън"],
            "query": {
                "entity_id": "sensor.outside_temp",
                "round": 0,
                "unit": "градуса",
                "unit_singular": "градус",
                "template": "В момента температурата навън е {value} {unit}.",
            },
        },
        "MovieMode": {
            "speak": False,
            "phrases": ["пусни филм"],
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
    def provider(self) -> str:
        return "gtts" if self._ready else "none"

    @property
    def voice(self) -> str:
        return "google-bg" if self._ready else "none"

    @property
    def unavailable_reason(self) -> str | None:
        return None if self._ready else "test disabled"

    def synthesize(self, text: str) -> AudioClip | None:
        return AudioClip(wav_bytes(), "audio/wav", "wav") if self._ready else None

    def close(self) -> None:
        return None


class FakeHa:
    def __init__(self, intent_text: str = "Готово.") -> None:
        self.intent_text = intent_text
        self.intents: list[str] = []
        self.services: list[tuple[str, dict]] = []

    async def handle_intent(self, intent: str) -> dict:
        self.intents.append(intent)
        return {"text": self.intent_text, "response_type": "action_done", "data": {}}

    async def call_service(
        self,
        service: str,
        target: dict | None = None,
        data: dict | None = None,
    ) -> dict:
        self.services.append((service, {"target": target or {}, "data": data or {}}))
        return {"changed": []}

    async def get_state(self, entity_id: str) -> dict:
        return {
            "entity_id": entity_id,
            "state": "10.7540130615234",
            "attributes": {
                "unit_of_measurement": "°C",
                "friendly_name": "Outside Temperature Outside",
            },
        }

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def build_runtime(
    *,
    transcript: str = "изключи лампата в хола",
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
        ha={"home": FakeHa()},
        default_instance="home",
    )


def client_for(runtime: Runtime, *, api_key: str | None = None) -> TestClient:
    settings = Settings(api_key=api_key)

    async def builder(_: Settings) -> Runtime:
        return runtime

    return TestClient(create_app(builder=builder, settings=settings))


def test_action_calls_service_and_returns_text_only() -> None:
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
    service, payload = runtime.ha["home"].services[0]
    assert service == "light.turn_off"
    assert payload["target"] == {"entity_id": "light.hall"}


def test_query_reads_state_and_returns_audio() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post(
            "/v1/command", json={"text": "каква е температурата навън"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "query"
        assert body["speak"] is True
        assert body["text"] == "В момента температурата навън е 11 градуса."
        assert body["audio_url"]

        audio = client.get(body["audio_url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.content[:4] == b"RIFF"


def test_speak_can_be_overridden_off() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        body = client.post(
            "/v1/command",
            json={"text": "каква е температурата навън", "speak": "false"},
        ).json()
    assert body["speak"] is False
    assert body["audio_url"] is None


def test_intent_mode_still_works() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        body = client.post("/v1/command", json={"text": "пусни филм"}).json()
    assert body["kind"] == "action"
    assert body["matched"]["intent"] == "MovieMode"
    assert runtime.ha["home"].intents == ["MovieMode"]


def test_no_match_does_not_touch_home_assistant() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post("/v1/command", json={"text": "пусни музика"})
    body = response.json()
    assert response.status_code == 200
    assert body["kind"] == "no_match"
    assert body["text"] == "Не разбрах. Повторете, моля."
    assert runtime.ha["home"].services == []
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
        assert (
            client.post(
                "/v1/command", json={"text": "изключи лампата в хола"}
            ).status_code
            == 401
        )
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
    assert health["tts"]["provider"] == "gtts"
    assert health["instances"]["home"] is True
    assert commands["language"] == "bg"
    by_intent = {entry["intent"]: entry for entry in commands["commands"]}
    assert by_intent["HallLightOff"]["mode"] == "action"
    assert by_intent["OutsideTemperature"]["mode"] == "query"
    assert by_intent["MovieMode"]["mode"] == "intent"


def test_unknown_instance_is_404() -> None:
    runtime = build_runtime()
    with client_for(runtime) as client:
        response = client.post(
            "/v1/command", json={"text": "изключи лампата в хола", "instance": "nope"}
        )
    assert response.status_code == 404
