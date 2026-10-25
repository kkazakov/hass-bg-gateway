# Hass BG Gateway

Bulgarian voice control for **one Home Assistant instance**, built for speed and
privacy: on-device speech recognition, a small fixed command catalogue, and no
LLM in the request path.

Say **„изключи лампата“** → the gateway transcribes it, matches the configured
phrase, calls `light.turn_off`, and returns a text reply in ~100 ms.
Sensor questions such as **„каква е температурата навън“** also return spoken
audio, with a female Bulgarian voice.

```
mic (16 kHz WAV)
      │
      ▼
sherpa-onnx streaming ASR (Bulgarian, int8, offline)
      │  transcript
      ▼
RapidFuzz phrase matcher + configured commands.yaml
      │  intent
      ▼
Home Assistant REST API  ──────────────►  service call
      │                                   entity state
      ▼
text reply  (+ TTS audio for speak: true)
```

## Features

- **Fast**: ~90–120 ms for actions, ~480 ms for a cold spoken query on a modern
  x86 CPU; cached query audio answers in ~100 ms.
- **Offline ASR**: `preslaff/bulgarian-asr-zipformer-streaming` via sherpa-onnx;
  no audio leaves the machine.
- **Fixed, declarative command set**: 2–20 phrases in `config/commands.yaml`.
  Add a command without writing code, then `POST /v1/reload`.
- **Three mapping modes**: call a service directly (`action`), read an entity and
  render a sentence (`query`), or call an HA `intent_script` by name.
- **Female Bulgarian TTS** by default (Google Translate TTS), with automatic
  fallback to a local Piper voice; can be switched fully offline.
- **Optional API key** on every `/v1` endpoint, for exposing the gateway through
  HTTPS. Empty by default.
- **One gateway = one Home Assistant**, so tokens, commands and state stay
  isolated per home. Run another container for a second HA.
- Docker image with models baked in; `docker compose up --build` is enough.

## Quick start with Docker

### 1. Get a Home Assistant token

In Home Assistant: **Profile → Security → Long-lived access tokens → Create
token**. Copy it once; it is shown only once. The token has full HA access, so
treat it like a password.

### 2. Configure

```bash
git clone https://github.com/kkazakov/hass-bg-gateway.git
cd hass-bg-gateway

cp .env.example .env
$EDITOR .env
```

Minimum:

```dotenv
BGV_HA_URL=http://homeassistant.local:8123
BGV_HA_TOKEN=eyJ...
BGV_API_KEY=          # leave empty on a trusted LAN; set for HTTPS exposure
```

`BGV_HA_URL` must be reachable **from the container**. If HA runs on the same
host, `http://host.docker.internal:8123` works on Docker Desktop; on Linux use
the host's LAN IP or a shared Docker network.

### 3. Start

```bash
docker compose up -d --build
docker compose logs -f gateway
```

The first build installs dependencies and downloads the Bulgarian ASR model and
Piper voice (~130 MB); expect a few minutes. Later starts are immediate.

### 4. Check it

```bash
# no API key:
curl -s http://127.0.0.1:8080/v1/health?deep=true | python3 -m json.tool

# with a key:
curl -s -H "X-API-Key: $BGV_API_KEY" \
  http://127.0.0.1:8080/v1/health?deep=true | python3 -m json.tool
```

Expected: `status: ok`, `asr.ready: true`, `tts.ready: true`, `ha.ready: true`.

### 5. Run a command (text, then audio)

The starter catalogue points at `light.living_room` and
`sensor.outside_temperature`. Replace them in `config/commands.yaml` with real
entities first; otherwise the gateway returns `502` (entity/service not found).
The sample WAVs match the starter phrases.

```bash
curl -s -X POST http://127.0.0.1:8080/v1/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"изключи лампата"}' | python3 -m json.tool

curl -s -X POST http://127.0.0.1:8080/v1/voice \
  -F audio=@examples/light-off.wav | python3 -m json.tool
```

With an API key, add `-H "X-API-Key: $BGV_API_KEY"` to both.

The starter `config/commands.yaml` uses placeholder entities
(`light.living_room`, `sensor.outside_temperature`). Replace them with your own,
then reload:

```bash
$EDITOR config/commands.yaml
curl -s -X POST http://127.0.0.1:8080/v1/reload \
  -H "X-API-Key: $BGV_API_KEY"
```

For a fuller real-home catalogue (hall light, night light, all-lights scenes,
indoor/outdoor temperature), use `config/commands-example.yaml`:

```bash
# either copy it over the starter:
cp config/commands-example.yaml config/commands.yaml
# or keep both and select it in .env:
echo 'BGV_COMMANDS_FILE=commands-example.yaml' >> .env
```

## Configuration

All settings are environment variables (prefix `BGV_`) or entries in `.env`.
`docker compose` passes `.env` into the container.

| Variable | Default | Purpose |
|---|---|---|
| `BGV_HA_URL` | `http://homeassistant.local:8123` | Base URL of the single HA instance |
| `BGV_HA_TOKEN` | *(empty)* | Long-lived access token; required |
| `BGV_HA_VERIFY_TLS` | `true` | Verify HA's TLS certificate |
| `BGV_HA_TIMEOUT_S` | `10` | Timeout for HA HTTP calls |
| `BGV_API_KEY` | *(empty)* | If set, all `/v1` endpoints require `X-API-Key` |
| `BGV_ASR_THREADS` | `4` | ONNX threads (use physical cores) |
| `BGV_HOTWORDS_SCORE` | `3.0` | Strength of hotword biasing |
| `BGV_LEAD_SILENCE_S` | `0.2` | Silence prepended to uploaded audio |
| `BGV_TAIL_SILENCE_S` | `1.0` | Silence appended; too short cuts final syllables |
| `BGV_TTS_ENABLED` | `true` | Enable audio for `speak: true` commands |
| `BGV_TTS_PROVIDER` | `gtts` | `gtts` (female, cloud) or `piper` (male, local) |
| `BGV_TTS_LANGUAGE` | `bg` | gTTS language |
| `BGV_TTS_TIMEOUT_S` | `5` | gTTS timeout before Piper fallback |
| `BGV_CONFIG_DIR` | `config` | Directory containing the command catalogue |
| `BGV_COMMANDS_FILE` | `commands.yaml` | Catalogue file inside `BGV_CONFIG_DIR` |
| `BGV_MODELS_DIR` | `models` | ASR/Piper models and TTS cache |
| `BGV_LOG_LEVEL` | `INFO` | Python log level |

### One gateway, one Home Assistant

There is intentionally **no instance switcher**. A gateway is bound to exactly
one `BGV_HA_URL`/`BGV_HA_TOKEN`. To control a second home, run a second container
with its own `.env` and port (see [Multiple HA instances](#multiple-ha-instances)).

## Command catalogue (`config/commands.yaml`)

`config/commands.yaml` is the minimal starter. `config/commands-example.yaml`
contains a full real-home example; select it with
`BGV_COMMANDS_FILE=commands-example.yaml`.

```yaml
language: bg

match:
  threshold: 88   # RapidFuzz WRatio, 0-100
  margin: 5       # best must beat the runner-up by this much

commands:
  LightOff:
    speak: false
    phrases:
      - "изключи лампата"
      - "изгаси лампата"
    hotwords: ["лампата"]
    action:
      service: light.turn_off
      target:
        entity_id: light.living_room   # <-- your entity
      response: "Изключих лампата."
```

### Mapping modes

| Fields | What runs | HA setup |
|---|---|---|
| `action:` | `POST /api/services/<domain>/<service>` | none |
| `query:` | `GET /api/states/<entity_id>`, then template | none |
| neither | `POST /api/intent/handle` by intent name | `intent_script` entry required |

An `action` may also carry `data:` (e.g. brightness). `target` fields are sent
top-level, because HA 2026.10 rejects a nested `target` on the REST service API.

A `query` must set `speak: true`; it supports rounding, units and a Bulgarian
template:

```yaml
  OutsideTemperature:
    speak: true
    phrases: ["каква е температурата навън", "колко градуса е навън"]
    hotwords: ["температурата", "навън"]
    query:
      entity_id: sensor.outside_temp
      round: 0
      unit: "градуса"
      unit_singular: "градус"
      template: "В момента температурата навън е {value} {unit}."
```

Template placeholders: `{value}` (localised with a decimal comma), `{unit}`,
`{name}` (HA friendly name), `{state}` (raw state). `unit_singular` is used when
the rounded value is exactly 1. `round: null` keeps the raw precision.

Intent mode needs a matching HA entry, e.g. in `configuration.yaml`:

```yaml
intent_script:
  CorridorLightOn:
    speech:
      text: "Светнах лампата в коридора."
    action:
      service: light.turn_on
      target:
        entity_id: light.corridor
```

An example file is in [`homeassistant/intent_script.yaml`](homeassistant/intent_script.yaml).

### Matching behaviour

- **Exact configured phrases win immediately**, even when a near-identical
  phrase exists (`навън` vs `отвън`).
- **Opposite verbs are rejected.** „включи“ (on) never fuzzy-matches „изключи“
  (off); the reply is „Не разбрах“ instead of the wrong action. Verbs are
  grouped on/off, open/close, raise/lower.
- **Near-ties are ambiguous** unless both candidates belong to the same command.
- **Hotwords** bias the ASR toward device names (`лампата`, `хола`). They are
  collected from all commands; add a word if the ASR consistently mishears it.
- Duplicate phrases across two commands are rejected at load time.
- After editing, `POST /v1/reload` re-reads the file and reloads the ASR model
  (~1–2 s); in-flight requests finish on the old runtime.

## API

Base URL: `http://127.0.0.1:8080`. Interactive docs: `/docs`.

Authentication is disabled when `BGV_API_KEY` is empty. When set, **every `/v1`
endpoint** — health, commands, voice, audio, reload — requires:

```
X-API-Key: <BGV_API_KEY>
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Service name (public) |
| `GET` | `/v1/health` | Liveness/readiness; `?deep=true` pings HA |
| `GET` | `/v1/commands` | Configured intents, phrases and modes |
| `POST` | `/v1/voice` | Multipart WAV upload, field `audio` |
| `POST` | `/v1/voice/raw` | Raw WAV body (MCU friendly) |
| `POST` | `/v1/command` | Text input, for tests and integrations |
| `GET` | `/v1/audio/{file}` | Generated reply audio (MP3 or WAV) |
| `POST` | `/v1/reload` | Rebuild runtime after editing the selected catalogue |

Text request:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/command \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $BGV_API_KEY" \
  -d '{"text":"каква е температурата навън","speak":"auto"}'
```

`speak` is `auto` (command default), `true` or `false`.

Response:

```json
{
  "kind": "query",
  "speak": true,
  "text": "В момента температурата навън е 12 градуса.",
  "transcript": "каква е температурата навън",
  "audio_url": "/v1/audio/6dbf....mp3",
  "matched": {
    "intent": "OutsideTemperature",
    "phrase": "каква е температурата навън",
    "score": 100.0,
    "runner_up": 0.0
  },
  "timings": {"asr_ms": 84.9, "match_ms": 0.05, "ha_ms": 6.6, "tts_ms": 371.2, "total_ms": 476.8},
  "reason": null
}
```

- `kind`: `action`, `query`, or `no_match`.
- `audio_url` is relative to the gateway and requires the API key when enabled.
- Audio is cached in memory (64 clips) and lost on restart; re-ask to regenerate.
- Errors: `401` bad/missing key, `413` audio too large, `422` not 16 kHz mono
  PCM16 WAV, `502` HA unreachable/rejected.

## Docker deployment

`docker-compose.yaml` builds the image, injects `.env`, mounts `config/` and a
named volume for the TTS cache.

```bash
docker compose up -d --build     # first build downloads models
docker compose logs -f gateway
docker compose restart gateway
docker compose down              # keeps the tts-cache volume
docker compose up -d             # update after git pull
```

- **Models are baked into the image**, so there is no model bind mount. Rebuild
  the image after updating `models/fetch_models.sh`.
- `./config` is mounted read-only; edit `commands.yaml` (or the file named by
  `BGV_COMMANDS_FILE`) on the host and call `/v1/reload`.
- The TTS cache lives in the `tts-cache` volume; removing it only costs one
  re-synthesis per phrase.
- The container runs as a non-root user and exposes `8080`.

### Ports and LAN/tailnet access

The default publish is **loopback only**:

```yaml
ports:
  - "127.0.0.1:8080:8080"
```

For direct access from a phone on the same LAN/tailnet, change it to:

```yaml
ports:
  - "8080:8080"        # LAN
  # or: "100.x.y.z:8080:8080"  # Tailscale IP only
```

Keep the API key enabled if more than one trusted device can reach it.

### HTTPS with Caddy

Put a reverse proxy in front and keep the container on loopback. Caddy obtains
and renews certificates automatically:

```caddyfile
gateway.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Then generate a key and put the value in `.env`:

```bash
openssl rand -hex 32
```

```dotenv
BGV_API_KEY=<paste the generated value>
```

Every client must send `X-API-Key`. Caddy handles TLS; do not publish port 8080
to the internet directly. `/docs` and `/openapi.json` remain public, so block
them at the proxy if you do not want to expose the schema.

### Multiple HA instances

Run one compose project per home, with a distinct host port and container name:

```bash
cp .env.example .env.ha2          # second HA URL/token, different API key
cp docker-compose.yaml docker-compose.ha2.yaml
# edit docker-compose.ha2.yaml:
#   container_name: hass-bg-gateway-ha2
#   env_file: .env.ha2
#   ports: "127.0.0.1:8081:8080"
docker compose -f docker-compose.ha2.yaml up -d --build
```

Each gateway has its own catalogue only if the intents differ; otherwise mount
the same file read-only.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

./models/fetch_models.sh        # ASR (~70 MB) + Piper voice (~61 MB)
cp .env.example .env
$EDITOR .env

uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Python 3.12+ is supported. `pytest -q` runs the API/matcher/audio tests with a
fake runtime; no models, network or Home Assistant are needed.

```bash
pytest -q          # 39 tests
```

## Latency

Measured on a Ryzen AI 9 HX 370 (warm models, this service, real HA):

| Stage | Value |
|---|---|
| ASR, 2.2 s command + 1 s padding | 60–90 ms |
| Phrase match (tens of phrases) | 0.1–0.2 ms |
| HA service call / state read (LAN) | 5–60 ms |
| gTTS (female, 3.8 s answer) | 350–450 ms |
| Piper (local fallback) | ~80 ms |
| **Action end to end** | **90–120 ms** |
| **Spoken query, cold** | **~480 ms** |
| **Spoken query, cached audio** | **~100 ms** |

An N100/Pi-class CPU is roughly 2–4× slower on ASR, still inside 500 ms for
actions. gTTS is network-bound and dominates spoken queries; use
`BGV_TTS_PROVIDER=piper` for an offline, faster but male voice.

## Security

- **API key**: constant-time comparison, applied to every `/v1` route. Empty by
  default for a trusted LAN.
- **Bind loopback** in compose and terminate HTTPS at Caddy/nginx. Do not expose
  `uvicorn` directly to the internet.
- **HA token**: full access to Home Assistant. Keep `.env` mode `600`, never
  commit it; rotate the token if it leaks.
- **No cloud ASR/LLM**: audio never leaves the machine. With the default gTTS
  provider, only the *answer text* of `speak: true` commands is sent to Google;
  set `BGV_TTS_PROVIDER=piper` to keep everything local.
- There is no rate limiting or user management; use the reverse proxy for that.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `BGV_HA_TOKEN is empty` at startup | Fill `BGV_HA_TOKEN` in `.env`; recreate the container |
| `401 invalid or missing X-API-Key` | Send `X-API-Key` on **every** `/v1` call, including health/audio |
| `502` from the gateway | HA unreachable or rejected the call; check `docker compose logs` |
| HA returns `400` for a service | Target fields must be top-level; the client already flattens them |
| `422` on `/v1/voice` | Audio must be 16 kHz mono PCM16 WAV |
| `no_match` for a phrase you said | Add the phrase/hotwords, check `/v1/commands`, reload |
| „Не разбрах“ for „включи…“ | No on-command configured, or it was rejected as the opposite of „изключи“ |
| Female voice missing / male voice | gTTS blocked; check network. `BGV_TTS_PROVIDER=piper` is the offline fallback |
| Docker build fails downloading models | Retry the build; Hugging Face may be rate-limiting |
| Slow ASR | Set `BGV_ASR_THREADS` to physical cores; avoid oversubscribing |
| TTS audio 404 | Audio cache is in memory and resets on restart; ask again |

## Project layout

```
app/
  main.py              FastAPI app factory, lifespan, optional API key
  config.py            Settings + command-catalogue models
  asr.py               sherpa-onnx streaming ASR, hotwords
  matcher.py           normalisation, fuzzy matching, antonym guard
  ha.py                single Home Assistant REST client
  reply.py             query templates, units, decimal comma
  tts.py               gTTS + Piper fallback, caching
  runtime.py           wiring and lifecycle
  api/routes/          voice, health, admin
config/
  commands.yaml        starter phrases with placeholder entities
  commands-example.yaml full real-home example (scenes, temperatures)
examples/
  *.wav                ready-to-post Bulgarian command samples
homeassistant/
  intent_script.yaml   optional example for intent mode
models/
  fetch_models.sh      ASR + Piper download, bpe.vocab generation
tests/                 API, matcher, audio, reply tests (fake runtime)
Dockerfile
docker-compose.yaml
.env.example
```

## Android client

A push-to-talk Android app lives in a separate repository
(`hass-bulgarian-voice`): hold the mic, release to send, multiple gateway URLs.
It does **not** send `X-API-Key` yet, so keep the gateway on the LAN without a
key, or add the header in the app before exposing it.

## Limits / roadmap

- Fixed phrases only; this is deliberate (predictable, fast, no LLM).
- One HA per gateway; run multiple containers for multiple homes.
- No streaming partials or wake word.
- ESP32 satellite (button + mic + amp) is not built yet.
- Query TTS with gTTS requires internet; Piper is fully offline but male.
