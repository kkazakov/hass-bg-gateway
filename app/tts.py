"""Bulgarian text-to-speech for spoken query answers.

Two providers:

* ``gtts``  — Google Translate TTS, Bulgarian female voice (~213 Hz, ~0.45 s).
              Needs internet; text leaves the machine. This is the default so
              the assistant does not sound like the local male Piper voice.
* ``piper`` — local `bg_BG-dimitar-medium` (~104 Hz, ~0.08 s, offline, male).

The service tries the configured provider and falls back to the other one, so a
network failure degrades to a local answer instead of silence.

Action replies never use TTS; only commands marked ``speak: true``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import logging
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PIPER_VOICE = "bg_BG-dimitar-medium"
PIPER_SUBDIR = "piper"
CACHE_SUBDIR = "tts-cache"


class TtsError(RuntimeError):
    """Raised when synthesis fails."""


@dataclass(frozen=True)
class AudioClip:
    """Synthesized audio plus what a client needs to play it."""

    data: bytes
    media_type: str
    extension: str


class PiperTts:
    """Local Piper voice (male, offline)."""

    def __init__(self, models_dir: Path) -> None:
        self._voice_dir = models_dir / PIPER_SUBDIR
        self._cache_dir = models_dir / CACHE_SUBDIR
        self._lock = threading.Lock()
        self._voice = None
        self._unavailable_reason: str | None = None

        model_path = self._voice_dir / f"{PIPER_VOICE}.onnx"
        config_path = self._voice_dir / f"{PIPER_VOICE}.onnx.json"
        if not model_path.exists() or not config_path.exists():
            self._unavailable_reason = f"voice not found in {self._voice_dir}"
            log.warning("Piper unavailable: %s", self._unavailable_reason)
            return

        try:
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(model_path))
        except Exception as exc:  # pragma: no cover - environment issue
            self._unavailable_reason = f"piper failed to load: {exc}"
            log.warning("Piper unavailable: %s", self._unavailable_reason)
            return

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        log.info("Piper voice ready: %s", PIPER_VOICE)

    @property
    def ready(self) -> bool:
        return self._voice is not None

    @property
    def reason(self) -> str | None:
        return None if self.ready else self._unavailable_reason

    def synthesize(self, text: str) -> AudioClip | None:
        if self._voice is None or not text.strip():
            return None

        key = hashlib.sha256(f"piper\0{PIPER_VOICE}\0{text}".encode()).hexdigest()[:16]
        cached = self._cache_dir / f"{key}.wav"
        if cached.exists():
            return AudioClip(cached.read_bytes(), "audio/wav", "wav")

        buffer = io.BytesIO()
        with self._lock:
            try:
                with wave.open(buffer, "wb") as wav:
                    self._voice.synthesize_wav(text, wav)
            except Exception as exc:  # pragma: no cover - native failure
                raise TtsError(f"piper synthesis failed: {exc}") from exc

        data = buffer.getvalue()
        try:
            cached.write_bytes(data)
        except OSError:
            log.warning("could not write TTS cache file %s", cached)
        return AudioClip(data, "audio/wav", "wav")


class GoogleTts:
    """Google Translate TTS (female Bulgarian voice, needs internet)."""

    def __init__(
        self,
        models_dir: Path,
        *,
        language: str = "bg",
        timeout_s: float = 5.0,
    ) -> None:
        self._language = language
        self._timeout_s = timeout_s
        self._cache_dir = models_dir / CACHE_SUBDIR
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="gtts"
        )
        self._available = importlib.util.find_spec("gtts") is not None
        if self._available:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            log.info("Google TTS ready (language=%s, female)", language)
        else:
            log.warning("google TTS unavailable: gTTS package is not installed")

    @property
    def ready(self) -> bool:
        return self._available

    @property
    def voice(self) -> str:
        return f"google-{self._language}"

    def _synth(self, text: str) -> bytes:
        from gtts import gTTS

        buffer = io.BytesIO()
        gTTS(text=text, lang=self._language, slow=False).write_to_fp(buffer)
        return buffer.getvalue()

    def synthesize(self, text: str) -> AudioClip | None:
        if not self._available or not text.strip():
            return None

        key = hashlib.sha256(
            f"gtts\0{self._language}\0{text}".encode()
        ).hexdigest()[:16]
        cached = self._cache_dir / f"{key}.mp3"
        if cached.exists():
            return AudioClip(cached.read_bytes(), "audio/mpeg", "mp3")

        future = self._executor.submit(self._synth, text)
        try:
            data = future.result(timeout=self._timeout_s)
        except FutureTimeout as exc:
            raise TtsError(f"google TTS timed out after {self._timeout_s}s") from exc
        except Exception as exc:
            raise TtsError(f"google TTS failed: {exc}") from exc

        if not data:
            raise TtsError("google TTS returned no audio")
        try:
            cached.write_bytes(data)
        except OSError:
            log.warning("could not write TTS cache file %s", cached)
        return AudioClip(data, "audio/mpeg", "mp3")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class TtsService:
    """Configured provider with automatic fallback."""

    def __init__(
        self,
        models_dir: Path,
        *,
        provider: str = "gtts",
        language: str = "bg",
        enabled: bool = True,
        timeout_s: float = 5.0,
    ) -> None:
        self._enabled = enabled
        self._requested = provider.strip().lower()
        self._piper: PiperTts | None = None
        self._google: GoogleTts | None = None

        if not enabled:
            log.info("TTS disabled by configuration")
            return

        if self._requested == "gtts":
            self._google = GoogleTts(
                models_dir, language=language, timeout_s=timeout_s
            )
        self._piper = PiperTts(models_dir)

        if not self.ready:
            log.warning("no TTS provider available; query answers will be text only")

    @property
    def ready(self) -> bool:
        return bool(
            (self._google and self._google.ready) or (self._piper and self._piper.ready)
        )

    @property
    def provider(self) -> str:
        if self._google and self._google.ready:
            return "gtts"
        if self._piper and self._piper.ready:
            return "piper"
        return "none"

    @property
    def voice(self) -> str:
        if self.provider == "gtts" and self._google:
            return self._google.voice
        if self.provider == "piper":
            return PIPER_VOICE
        return "none"

    @property
    def unavailable_reason(self) -> str | None:
        if self.ready:
            return None
        if self._requested == "gtts":
            return "gTTS unavailable and no local Piper voice"
        return "no local Piper voice"

    def synthesize(self, text: str) -> AudioClip | None:
        if not self._enabled or not text.strip():
            return None

        if self._google and self._google.ready:
            try:
                clip = self._google.synthesize(text)
                if clip:
                    return clip
            except TtsError as exc:
                log.warning("%s; falling back to local voice", exc)

        if self._piper and self._piper.ready:
            try:
                return self._piper.synthesize(text)
            except TtsError as exc:
                log.warning("%s", exc)

        return None

    def close(self) -> None:
        if self._google:
            self._google.close()
