"""Bulgarian streaming ASR via sherpa-onnx.

Model: preslaff/bulgarian-asr-zipformer-streaming (int8, ~70 MB encoder).
See ``models/fetch_models.sh`` for download + ``bpe.vocab`` generation.

The recognizer is created once at startup and kept warm (~1.3 s load on a
modern x86 CPU, ~52 ms to decode a 2 s command). It is not thread-safe, so
calls are serialised with a lock.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL_SUBDIR = "bg-streaming"
REQUIRED_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
)


class AsrError(RuntimeError):
    """Raised when the model is missing or inference fails."""


class AsrEngine:
    def __init__(
        self,
        models_dir: Path,
        *,
        threads: int = 4,
        hotwords: list[str] | None = None,
        hotwords_score: float = 3.0,
    ) -> None:
        self._model_dir = models_dir / MODEL_SUBDIR
        self._threads = max(1, threads)
        self._lock = threading.Lock()
        self._hotwords = [w for w in (hotwords or []) if w.strip()]
        self._hotwords_score = float(hotwords_score)

        missing = [name for name in REQUIRED_FILES if not (self._model_dir / name).exists()]
        if missing:
            raise AsrError(
                f"missing model files in {self._model_dir}: {', '.join(missing)}. "
                "Run models/fetch_models.sh first."
            )

        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - environment issue
            raise AsrError("sherpa-onnx is not installed") from exc

        self._sherpa = sherpa_onnx
        self._recognizer = self._build_recognizer()

    def _hotwords_path(self) -> Path | None:
        if not self._hotwords:
            return None
        vocab = self._model_dir / "bpe.vocab"
        if not vocab.exists():
            log.warning(
                "hotwords configured but %s is missing; scoring without hotwords",
                vocab,
            )
            return None
        path = self._model_dir / "hotwords.txt"
        path.write_text("\n".join(self._hotwords) + "\n", encoding="utf-8")
        return path

    def _build_recognizer(self):
        common = dict(
            tokens=str(self._model_dir / "tokens.txt"),
            encoder=str(self._model_dir / "encoder.int8.onnx"),
            decoder=str(self._model_dir / "decoder.int8.onnx"),
            joiner=str(self._model_dir / "joiner.int8.onnx"),
            num_threads=self._threads,
            sample_rate=16_000,
            feature_dim=80,
            enable_endpoint_detection=False,
            decoding_method="greedy_search",
        )
        hotwords = self._hotwords_path()
        if hotwords is not None:
            common.update(
                decoding_method="modified_beam_search",
                max_active_paths=4,
                hotwords_file=str(hotwords),
                hotwords_score=self._hotwords_score,
                modeling_unit="bpe",
                bpe_vocab=str(self._model_dir / "bpe.vocab"),
            )
        log.info(
            "loading ASR model (%s threads, hotwords=%d)",
            self._threads,
            len(self._hotwords),
        )
        recognizer = self._sherpa.OnlineRecognizer.from_transducer(**common)
        log.info("ASR model ready")
        return recognizer

    @property
    def hotwords_score(self) -> float:
        return self._hotwords_score

    @hotwords_score.setter
    def hotwords_score(self, value: float) -> None:
        self._hotwords_score = value

    def transcribe(self, samples: np.ndarray, *, chunk: int = 1600) -> str:
        """Decode float32 16 kHz mono samples (already padded) into text."""
        if samples.size == 0:
            return ""
        with self._lock:
            stream = self._recognizer.create_stream()
            try:
                for start in range(0, len(samples), chunk):
                    stream.accept_waveform(16_000, samples[start : start + chunk])
                    while self._recognizer.is_ready(stream):
                        self._recognizer.decode_stream(stream)
                stream.input_finished()
                while self._recognizer.is_ready(stream):
                    self._recognizer.decode_stream(stream)
                return str(self._recognizer.get_result(stream)).strip()
            except Exception as exc:  # pragma: no cover - native failure
                raise AsrError(f"ASR inference failed: {exc}") from exc
