from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from app.audio import AudioError, decode_wav, duration_s, pad


def wav_bytes(
    samples: np.ndarray,
    *,
    rate: int = 16_000,
    channels: int = 1,
    width: int = 2,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
        if channels > 1:
            pcm = np.repeat(pcm, channels)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def test_decode_mono_16k() -> None:
    samples = np.zeros(1600, dtype=np.float32)
    decoded = decode_wav(wav_bytes(samples))
    assert decoded.shape == (1600,)
    assert decoded.dtype == np.float32


def test_rejects_stereo() -> None:
    with pytest.raises(AudioError, match="mono"):
        decode_wav(wav_bytes(np.zeros(800, dtype=np.float32), channels=2))


def test_rejects_other_sample_rate() -> None:
    with pytest.raises(AudioError, match="16000"):
        decode_wav(wav_bytes(np.zeros(800, dtype=np.float32), rate=8_000))


def test_rejects_8bit() -> None:
    with pytest.raises(AudioError, match="16-bit"):
        decode_wav(wav_bytes(np.zeros(800, dtype=np.float32), width=1))


def test_rejects_garbage() -> None:
    with pytest.raises(AudioError):
        decode_wav(b"not a wav file")


def test_pad_adds_expected_duration() -> None:
    samples = np.ones(16_000, dtype=np.float32)
    padded = pad(samples, lead_s=0.2, tail_s=0.6)
    assert duration_s(padded) == pytest.approx(1.8)
    assert padded[0] == 0.0 and padded[-1] == 0.0
