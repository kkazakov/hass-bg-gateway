"""Audio decoding helpers: strict 16 kHz mono PCM16 WAV in, float32 samples out."""

from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE = 16_000


class AudioError(ValueError):
    """Raised when the uploaded audio is not 16 kHz mono PCM16."""


def decode_wav(data: bytes) -> np.ndarray:
    """Decode WAV bytes into float32 mono samples in ``[-1, 1]`` at 16 kHz."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"not a readable WAV file: {exc}") from exc

    if channels != 1:
        raise AudioError(f"expected mono audio, got {channels} channels")
    if width != 2:
        raise AudioError(f"expected 16-bit PCM, got {width * 8}-bit samples")
    if rate != SAMPLE_RATE:
        raise AudioError(f"expected {SAMPLE_RATE} Hz audio, got {rate} Hz")
    if not frames:
        raise AudioError("audio file is empty")

    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return samples


def pad(
    samples: np.ndarray,
    *,
    lead_s: float = 0.0,
    tail_s: float = 0.0,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Add synthetic silence before/after the utterance.

    The streaming model improves noticeably with a little lead-in, and needs
    trailing context to flush its last chunk (see README: "why the padding").
    """
    lead = np.zeros(int(round(lead_s * sample_rate)), dtype=np.float32)
    tail = np.zeros(int(round(tail_s * sample_rate)), dtype=np.float32)
    return np.concatenate([lead, samples, tail])


def duration_s(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    return len(samples) / sample_rate
