# hass-bulgarian-commands

Bulgarian voice control for Home Assistant with a **small, fixed command set**.
Speak a configured phrase → the gateway transcribes it, matches it to a command,
and calls a Home Assistant `intent_script`. No cloud, no LLM, no general NLU.

```
mic → 16 kHz WAV → sherpa-onnx ASR (Bulgarian, int8) → fuzzy phrase match
    → POST /api/intent/handle {name} → intent_script → light.turn_off
    → text reply (actions)            → text + Piper audio (queries)
```

Measured on a Ryzen AI 9 HX 370 (warm models, this repo's service): **~60–90 ms ASR**
for a 2 s command (including 1 s of padding), **~0.2 ms** phrase match, ~15–60 ms
Home Assistant call — **~90–110 ms end to end**, comfortably inside a 500 ms
budget. TTS (only for `speak: true`) is ~76–78 ms.

---

## Why it is built this way

- **The command list is configuration, not inference.** You write ~10 intents in
  Home Assistant and 2–4 Bulgarian phrases per intent in `config/commands.yaml`.
  Adding a command is three edits and a reload.
- **The gateway matches phrases, Home Assistant owns devices.** HA's
  `intent_script` holds the entity ids and the reply text; the gateway only maps
  sound → intent name.
- **Intent by name, not free text.** `POST /api/intent/handle` is used because the
  conversation response does not include the matched intent name — the gateway
  could not otherwise know whether a reply is an action (text only) or a query
  (spoken).
- **Fuzzy matching absorbs ASR slips.** The model hears `ломпата` for `лампата`;
  RapidFuzz over the configured phrases still picks the right command (score
  ≥88, margin ≥5, otherwise "не разбрах").
- **Padding, not streaming.** A little lead-in and 1 s of synthetic tail make
  the streaming model flush its last chunk; no waiting for real silence.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

./models/fetch_models.sh                 # ASR model (~70 MB) + Piper voice (~61 MB)

cp config/instances.example.yaml config/instances.yaml
cp .env.example .env
# edit .env: HA_HOME_TOKEN=...           (HA profile → Security → long-lived token)
#            BGV_API_KEY=...              (optional but recommended)

uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Add the two entries from [`homeassistant/intent_script.yaml`](homeassistant/intent_script.yaml)
to your HA `configuration.yaml`, point them at your entities, and restart HA (or
reload `intent_script`).

### First check

```bash
curl -s localhost:8080/v1/health | jq
curl -s -X POST localhost:8080/v1/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"изключи лампата в хола"}' | jq
```

Expected: `kind: action`, `speak: false`, `matched.intent: HallLightOff`,
`text: "Изключих лампата в хола."`

### With audio

```bash
python scripts/make_test_wav.py "изключи лампата в хола" /tmp/hall.wav
curl -s -X POST localhost:8080/v1/voice -F audio=@/tmp/hall.wav | jq
```

---

## Configuration

### `config/commands.yaml`

```yaml
language: bg
match:
  threshold: 88      # RapidFuzz WRatio
  margin: 5          # best must beat second-best by this much

commands:
  HallLightOff:
    speak: false     # actions: text reply only
    phrases:
      - "изключи лампата в хола"
      - "изгаси лампата в хола"
    hotwords: ["лампата", "хола"]   # bias the ASR toward these words
```

`speak: true` is for queries (sensor values). The reply text comes from HA and is
synthesized with Piper.

### `config/instances.yaml`

```yaml
default: home
instances:
  home:
    url: http://homeassistant.local:8123
    token_env: HA_HOME_TOKEN
    verify_tls: true
```

Multiple homes: add entries and pass `?instance=office` per request. Clients can
be bound to one instance (recommended for wall devices).

### Adding a command

1. Add the `intent_script` entry in HA (what it does, and `speech.text`).
2. Add the intent + phrases to `config/commands.yaml` (how it sounds).
3. `curl -X POST localhost:8080/v1/reload` (reloads config and hotwords; ~1 s).

---

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/voice` | multipart `audio` (16 kHz mono PCM16 WAV, ≤2 MB) |
| `POST` | `/v1/voice/raw` | same, WAV as raw body (MCU friendly) |
| `POST` | `/v1/command` | `{"text": "...", "instance": "home", "speak": "auto"}` |
| `GET` | `/v1/audio/{id}.wav` | synthesized reply (queries only) |
| `GET` | `/v1/commands` | configured intents + phrases |
| `GET` | `/v1/health?deep=true` | readiness + per-instance reachability |
| `POST` | `/v1/reload` | re-read config, rebuild runtime |

All except `/v1/health` require `X-API-Key` when `BGV_API_KEY` is set.

Response:

```json
{
  "kind": "action",
  "speak": false,
  "text": "Изключих лампата в хола.",
  "transcript": "изключи лампата в хола",
  "audio_url": null,
  "instance": "home",
  "matched": {"intent": "HallLightOff", "phrase": "изключи лампата в хола",
              "score": 100.0, "runner_up": 78.4},
  "timings": {"asr_ms": 61.2, "match_ms": 0.6, "ha_ms": 34.1, "tts_ms": 0, "total_ms": 96.3}
}
```

`kind: no_match` means the transcript did not match any phrase; `reason` explains
why (below threshold / ambiguous). Log it and add a variant.

---

## Layout

```
app/
  main.py              FastAPI app factory (injectable builder for tests)
  config.py            settings + YAML models
  asr.py               sherpa-onnx streaming model, hotwords, thread lock
  matcher.py           normalise + RapidFuzz phrase matching
  ha.py                Home Assistant intent client
  tts.py               Piper (optional; actions never use it)
  runtime.py           wiring + lifecycle
  api/routes/          voice, health, admin
config/
  commands.yaml        the phrases (tune this when ASR mishears)
  instances.example.yaml
homeassistant/
  intent_script.yaml   the device mapping (copy into HA)
models/
  fetch_models.sh      ASR + Piper downloads, bpe.vocab generation
tests/                 matcher, audio, API (fake runtime; no model needed for tests)
```

## Tests

```bash
pytest          # matcher + audio + API with a fake runtime; no models required
```

The API tests inject a fake runtime (`create_app(builder=...)`), so they run in CI
without the 130 MB of models.

## Latency (measured 2026-10-25, warm models)

| Stage | Value |
|---|---|
| ASR, 2.2 s command + padding | 60–90 ms |
| Phrase match (30–40 phrases) | 0.1–0.2 ms |
| HA `intent/handle` (LAN) | 15–60 ms |
| Piper synthesis (1.6 s reply) | 78 ms |
| **Total, action** | **~90–110 ms** |
| **Total, spoken query (to audio)** | **~180–250 ms** |

Numbers are from a fast x86 CPU (AVX-512 VNNI). Expect 2–4× on an N100/Pi 5,
which still fits 500 ms.

## Limitations

- Only configured phrases (and close ASR variants) work. This is intentional.
- Audio must be **16 kHz mono PCM16 WAV**; the service rejects anything else.
- The ASR recognizer is serialised by a lock — fine for a home, not a call centre.
- HA's `intent_script` runs with full access; do not expose this API to the
  internet without `BGV_API_KEY` and a reverse proxy.
- Piper voice `bg_BG-dimitar-medium` is the only Bulgarian voice; replies are
  male and "medium" quality.

## Roadmap

- A tiny Android client (push-to-talk, multi-instance).
- ESP32-S3 satellite (button + mic + amp, POST `/v1/voice/raw`).
- Streaming WebSocket for live partials (not needed for the 500 ms target).
- Optionally, `conversation: intents:` in HA so the same phrases can be typed.
