#!/usr/bin/env bash
# Download the Bulgarian ASR model and the Piper voice.
# Run from the repository root:  ./models/fetch_models.sh
set -euo pipefail

MODELS_DIR="$(cd "$(dirname "$0")" && pwd)"
ASR_DIR="$MODELS_DIR/bg-streaming"
PIPER_DIR="$MODELS_DIR/piper"
BASE="https://huggingface.co/preslaff/bulgarian-asr-zipformer-streaming/resolve/main"

mkdir -p "$ASR_DIR" "$PIPER_DIR"

echo "==> Bulgarian streaming ASR (int8, ~70 MB encoder)"
declare -A FILES=(
  ["encoder-epoch-40-avg-6-chunk-32-left-128.int8.onnx"]="encoder.int8.onnx"
  ["decoder-epoch-40-avg-6-chunk-32-left-128.int8.onnx"]="decoder.int8.onnx"
  ["joiner-epoch-40-avg-6-chunk-32-left-128.int8.onnx"]="joiner.int8.onnx"
)
for remote in "${!FILES[@]}"; do
  local_name="${FILES[$remote]}"
  if [[ -s "$ASR_DIR/$local_name" ]]; then
    echo "    $local_name already present"
    continue
  fi
  echo "    downloading $local_name"
  curl -fL --retry 3 -o "$ASR_DIR/$local_name" "$BASE/$remote"
done

for name in tokens.txt bpe.model; do
  if [[ -s "$ASR_DIR/$name" ]]; then
    echo "    $name already present"
  else
    echo "    downloading $name"
    curl -fL --retry 3 -o "$ASR_DIR/$name" "$BASE/$name"
  fi
done

echo "==> bpe.vocab (needed for hotword biasing)"
if [[ ! -s "$ASR_DIR/bpe.vocab" ]]; then
  python3 - "$ASR_DIR" <<'PY'
import sys
from pathlib import Path

import sentencepiece as spm

asr = Path(sys.argv[1])
sp = spm.SentencePieceProcessor(model_file=str(asr / "bpe.model"))
with (asr / "bpe.vocab").open("w", encoding="utf-8") as out:
    for i in range(sp.get_piece_size()):
        token = sp.id_to_piece(i)
        if token.startswith("<") and token.endswith(">"):
            continue
        out.write(f"{token} {sp.get_score(i)}\n")
print(f"    wrote {sp.get_piece_size()} pieces")
PY
fi

echo "==> Piper Bulgarian voice (bg_BG-dimitar-medium, ~61 MB)"
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/bg/bg_BG/dimitar/medium"
for file in bg_BG-dimitar-medium.onnx bg_BG-dimitar-medium.onnx.json; do
  if [[ -s "$PIPER_DIR/$file" ]]; then
    echo "    $file already present"
  else
    echo "    downloading $file"
    curl -fL --retry 3 -o "$PIPER_DIR/$file" "$PIPER_BASE/$file"
  fi
done

echo
echo "Done. Models in $MODELS_DIR"
du -sh "$ASR_DIR" "$PIPER_DIR"
