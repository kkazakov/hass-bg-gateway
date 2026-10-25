"""Local Bulgarian text-to-speech via Piper.

Used only for commands marked ``speak: true`` (sensor-value queries). Action
replies are text-only by design, so this path is optional: if the voice is
missing the service keeps working and simply returns text.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
import wave
from pathlib import Path

log = logging.getLogger(__name__)

VOICE_NAME = "bg_BG-dimitar-medium"
VOICE_SUBDIR = "piper"


class TtsError(RuntimeError):
    """Raised when synthesis fails."""


class TtsEngine:
    def __init__(self, models_dir: Path, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._voice_dir = models_dir / VOICE_SUBDIR
        self._cache_dir = models_dir / "tts-cache"
        self._lock = threading.Lock()
        self._voice = None
        self._unavailable_reason: str | None = None

        if not enabled:
            self._unavailable_reason = "disabled by configuration"
            return

        model_path = self._voice_dir / f"{VOICE_NAME}.onnx"
        config_path = self._voice_dir / f"{VOICE_NAME}.onnx.json"
        if not model_path.exists() or not config_path.exists():
            self._unavailable_reason = f"voice not found in {self._voice_dir}"
            log.warning("TTS unavailable: %s (text replies only)", self._unavailable_reason)
            return

        try:
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(model_path))
        except Exception as exc:  # pragma: no cover - environment issue
            self._unavailable_reason = f"piper failed to load: {exc}"
            log.warning("TTS unavailable: %s", self._unavailable_reason)
            return

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        log.info("TTS voice ready: %s", VOICE_NAME)

    @property
    def ready(self) -> bool:
        return self._voice is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason if not self.ready else None

    def synthesize(self, text: str) -> bytes | None:
        """Return WAV bytes, or ``None`` when TTS is unavailable."""
        if self._voice is None or not text.strip():
            return None

        key = hashlib.sha256(f"{VOICE_NAME}\0{text}".encode()).hexdigest()[:16]
        cached = self._cache_dir / f"{key}.wav"
        if cached.exists():
            return cached.read_bytes()

        buffer = io.BytesIO()
        with self._lock:
            try:
                with wave.open(buffer, "wb") as wav:
                    self._voice.synthesize_wav(text, wav)
            except Exception as exc:  # pragma: no cover - native failure
                raise TtsError(f"synthesis failed: {exc}") from exc

        data = buffer.getvalue()
        try:
            cached.write_bytes(data)
        except OSError:
            log.warning("could not write TTS cache file %s", cached)
        return data
