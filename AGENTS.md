# AGENTS.md — hass-bg-gateway

Guidelines for agentic coding agents working in this repository.

## Project overview

A FastAPI gateway that maps Bulgarian speech (offline sherpa-onnx ASR) to a
small, declarative command catalogue and executes it against **one** Home
Assistant instance. No LLM, no cloud ASR, no per-request free-text inference.
Actions reply with text; `speak: true` queries also return audio (gTTS female,
Piper fallback).

- Python 3.12+, FastAPI, httpx, sherpa-onnx 2.0.0, RapidFuzz, pydantic-settings
- Optional `X-API-Key` on all `/v1` routes (`BGV_API_KEY`, empty = disabled)
- One gateway = one HA (`BGV_HA_URL` / `BGV_HA_TOKEN`)

## Directory structure

```
app/
  main.py            app factory, lifespan, router auth dependency
  config.py          Settings + command-catalogue models
  asr.py             sherpa-onnx streaming ASR, hotwords, thread lock
  matcher.py         normalise, fuzzy matching, verb/antonym guard
  ha.py              single HA REST client (intent/service/state)
  reply.py           query template rendering (units, decimal comma)
  tts.py             gTTS + Piper fallback, disk cache
  runtime.py         wiring + lifecycle + single HA client
  api/deps.py        get_runtime, require_api_key
  api/routes/        voice.py, health.py, admin.py
config/commands.yaml starter catalogue (placeholder entities)
config/commands-example.yaml full real-home example (BGV_COMMANDS_FILE)
models/fetch_models.sh downloads ASR + Piper, builds bpe.vocab
tests/               API/matcher/audio/reply tests with a fake runtime
```

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
./models/fetch_models.sh
cp .env.example .env    # BGV_HA_URL, BGV_HA_TOKEN
uvicorn app.main:app --host 127.0.0.1 --port 8125
pytest -q
```

Docker: `docker compose up -d --build`; models are baked into the image.

## Conventions

- Keep the request path free of LLMs and network calls except Home Assistant
  and (optional) gTTS.
- All API routes under `/v1` must stay behind `require_api_key`; the health
  route is intentionally protected too.
- One HA per process. Do not reintroduce an `instances` map; separate homes get
  separate containers.
- `commands.yaml` is the extension surface (or the file selected by
  `BGV_COMMANDS_FILE`); prefer adding a command over adding code.
- Keep query answers Bulgarian; decimal comma comes from `reply.format_value`.
- Tests must not require models, network or HA. Use `create_app(builder=...)`
  with the fake runtime in `tests/test_api.py`.

## Gotchas

- **HA 2026.10 rejects nested `target`** on `POST /api/services/...`; target
  fields must be top-level (the client flattens them).
- **Opposite verbs**: `включи` must never match `изключи`. `matcher._verb_classes`
  rejects cross-class matches; do not loosen this.
- **Exact phrases bypass the ambiguity margin**; near-ties for the same intent
  are allowed on purpose.
- **Tail padding**: default `BGV_TAIL_SILENCE_S=1.0`; shorter values truncate the
  last syllable of commands.
- **gTTS is cloud and ~400 ms**; it must never block an action. It falls back to
  Piper, and TTS failures must return text only, never a 500.
- **Audio route serves both MP3 and WAV**; `AudioClip` carries the media type.
- **Docker healthcheck** sends `X-API-Key` from `$BGV_API_KEY`; if you change the
  health auth, update both Dockerfile and compose.
- **The API key is optional**: empty string disables auth. Never log it.
- `.env` is gitignored and contains the HA token; keep mode 600.
