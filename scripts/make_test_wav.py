#!/usr/bin/env python3
"""Synthesize a test WAV with the Bulgarian Piper voice (16 kHz mono PCM16).

Usage:  python scripts/make_test_wav.py "изключи лампата в хола" out.wav
Requires the Piper voice in models/piper (run models/fetch_models.sh first).
"""
from __future__ import annotations

import argparse
import io
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "models" / "piper" / "bg_BG-dimitar-medium.onnx"
TARGET_RATE = 16_000


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return samples
    duration = len(samples) / source_rate
    target_length = int(round(duration * target_rate))
    source_x = np.linspace(0.0, duration, num=len(samples), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_length, endpoint=False)
    return np.interp(target_x, source_x, samples).astype(np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--voice", type=Path, default=VOICE)
    args = parser.parse_args()

    if not args.voice.exists():
        print(f"voice not found: {args.voice}\nRun models/fetch_models.sh first.", file=sys.stderr)
        return 1

    from piper import PiperVoice

    voice = PiperVoice.load(str(args.voice))
    raw = io.BytesIO()
    with wave.open(raw, "wb") as wav:
        voice.synthesize_wav(args.text, wav)

    raw.seek(0)
    with wave.open(raw, "rb") as wav:
        source_rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    samples = samples.astype(np.float32) / 32768.0
    resampled = resample(samples, source_rate, TARGET_RATE)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(TARGET_RATE)
        wav.writeframes(np.clip(resampled * 32767, -32768, 32767).astype("<i2").tobytes())

    print(
        f"wrote {args.output} ({len(resampled) / TARGET_RATE:.2f}s, {TARGET_RATE} Hz) "
        f"from {source_rate} Hz"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
