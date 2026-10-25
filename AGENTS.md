# AGENTS.md — hass-bg-gateway

Guidelines for agentic coding agents working in this repository.

## Project overview

A FastAPI gateway that maps Bulgarian speech (offline sherpa-onnx ASR) to a
small, declarative command catalogue and executes it against **one** Home
Assistant instance. No LLM, no cloud ASR, no per-request free-text inference.
Targets map noun phrases to entities; built-in on/off verbs are composed with
them at load time. Custom full-phrase commands (including `speak: true` queries)
remain available. Actions reply with text; queries also return audio (gTTS
female, Piper fallback).

- Python 3.12+, FastAPI, httpx, sherpa-onnx 2.0.0, RapidFuzz, pydantic-settings
- Optional `X-API-Key` on all `/v1` routes (`BGV_API_KEY`, empty = disabled)
- One gateway = one HA (`BGV_HA_URL` / `BGV_HA_TOKEN`)

## Directory structure

```
app/
  main.py            app factory, lifespan, router auth dependency
  config.py          Settings, target expansion, command-catalogue models
  asr.py             sherpa-onnx streaming ASR, hotwords, thread lock
  matcher.py         normalise, fuzzy matching, verb/antonym guard
  ha.py              single HA REST client (intent/service/state)
  reply.py           query template rendering (units, decimal comma)
  tts.py             gTTS + Piper fallback, disk cache
  runtime.py         wiring + lifecycle + single HA client
  api/deps.py        get_runtime, require_api_key
  api/routes/        voice.py, health.py, admin.py
config/commands-example.yaml tracked example to copy and adapt
                        (config/commands.yaml is per-deployment and gitignored)
models/fetch_models.sh downloads ASR + Piper, builds bpe.vocab
tests/               API/matcher/audio/reply tests with a fake runtime
```

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
./models/fetch_models.sh
cp .env.example .env    # BGV_HA_URL, BGV_HA_TOKEN
cp config/commands-example.yaml config/commands.yaml
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
  `BGV_COMMANDS_FILE`); it is per-deployment and gitignored. Start from
  `commands-example.yaml`; prefer adding a target or custom command over code.
- Use `targets:` for simple verb + noun device control (`лампите в хола` ->
  `switch.hall_lamp`) and `commands:` for queries, intent scripts and unusual
  services.
- Keep query answers Bulgarian; decimal comma comes from `reply.format_value`.
- Tests must not require models, network or HA. Use `create_app(builder=...)`
  with the fake runtime in `tests/test_api.py`.

## Gotchas

- **HA 2026.10 rejects nested `target`** on `POST /api/services/...`; target
  fields must be top-level (the client flattens them).
- **Opposite verbs**: `включи` must never match `изключи`. `matcher._verb_classes`
  rejects cross-class matches; do not loosen this. Configured `verbs:` extras are
  merged into the guard by `build_runtime`.
- **YAML 1.1 booleans**: unquoted `on:`/`off:` mapping keys load as booleans.
  `config._restore_direction_keys` repairs them for `verbs:` and `targets:`;
  keep that path when adding new on/off sections.
- **Target expansion is at load time**: `commands:` entries are expanded from
  `targets:` + verbs; custom commands override an identical generated phrase.
  Duplicate generated phrases across targets are rejected.
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
