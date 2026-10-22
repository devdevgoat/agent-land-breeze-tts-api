# Breeze TTS + Home Assistant integration

Connect the Breeze TTS API to Home Assistant so automations, scripts, and voice assistants can speak with a natural voice over your LAN.

> **No auth, no internet.** The API has no authentication. Keep it on your LAN only. See [AGENTS.md](AGENTS.md) for the full security rules.

> **Licence.** Breeze TTS 2 weights are **research and non-commercial use only** (BreezeBlue licence). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Prerequisites

| What | Check |
|---|---|
| Breeze TTS API running | `curl http://<tts-ip>:7860/health` returns `{"status":"ok","sample_rate":24000,"runtime":"mlx"}` |
| Home Assistant on same LAN | Can reach the TTS server IP from the HA host |
| A media player entity in HA | `media_player.living_room` or similar, to play the generated WAV |

Replace `<tts-ip>` with your server's LAN IP (e.g. `172.16.9.1`) and `<tts-port>` with the port (default `7860`) throughout.

## 1. Health sensor (optional)

Add to `configuration.yaml` to monitor the TTS server:

```yaml
rest:
  - resource: "http://<tts-ip>:<tts-port>/health"
    scan_interval: 60
    sensor:
      - name: "Breeze TTS Status"
        value_template: "{{ value_json.status }}"
        json_attributes:
          - sample_rate
          - runtime
```

Restart HA. The sensor `sensor.breeze_tts_status` shows `ok` when the server is reachable.

## 2. Shell commands for speech generation

Home Assistant's `rest_command` doesn't support `multipart/form-data`, so we use `shell_command` with curl.

Add to `configuration.yaml`:

```yaml
shell_command:
  # Generate speech via the warm endpoint and return the response URL.
  # Usage: service call with data: { text: "Hello world" }
  breeze_tts_say: >-
    curl -sf -X POST "http://<tts-ip>:<tts-port>/v1/audio/speech/warm"
    -F "text={{ text }}"
    -F "instruction={{ instruction | default('A calm, friendly adult voice speaking clearly.') }}"
    -F "cfg_scale={{ cfg_scale | default(1) }}"
    -F "seed={{ seed | default(42) }}"
    -o /dev/null

  # Download speech as a WAV file to /config/www/ for playback.
  breeze_tts_download: >-
    ID=$(curl -sf -X POST "http://<tts-ip>:<tts-port>/v1/audio/speech/warm"
    -F "text={{ text }}"
    -F "instruction={{ instruction | default('A calm, friendly adult voice speaking clearly.') }}"
    -F "cfg_scale={{ cfg_scale | default(1) }}"
    -F "seed={{ seed | default(42) }}"
    | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])");
    sleep {{ wait | default(8) }};
    curl -sf "http://<tts-ip>:<tts-port>/response/$ID" -o /config/www/tts_latest.wav
```

## 3. Script: speak and play

This script generates speech and plays it on a media player:

```yaml
script:
  breeze_tts_speak:
    alias: "Breeze TTS Speak"
    description: "Generate speech and play it on a media player."
    fields:
      text:
        description: "Text to speak"
        required: true
        example: "Hello! Dinner is ready."
        selector:
          text:
      media_player:
        description: "Media player entity to play on"
        required: true
        example: "media_player.living_room"
        selector:
          entity:
            domain: media_player
      instruction:
        description: "Voice description"
        default: "A calm, friendly adult voice speaking clearly."
        selector:
          text:
      wait_seconds:
        description: "Seconds to wait for generation (longer text needs more)"
        default: 8
        selector:
          number:
            min: 3
            max: 30
    sequence:
      - service: shell_command.breeze_tts_download
        data:
          text: "{{ text }}"
          instruction: "{{ instruction }}"
          wait: "{{ wait_seconds }}"
      - delay:
          seconds: 1
      - service: media_player.play_media
        target:
          entity_id: "{{ media_player }}"
        data:
          media_content_id: "/local/tts_latest.wav"
          media_content_type: "music"
```

### Usage from an automation

```yaml
automation:
  - alias: "Announce dinner"
    trigger:
      - platform: time
        at: "18:30:00"
    action:
      - service: script.breeze_tts_speak
        data:
          text: "Dinner is ready! Come to the kitchen."
          media_player: media_player.living_room
```

## 4. Direct URL playback (simpler, if your media player supports HTTP)

If your media player can play HTTP URLs directly, skip the file download:

```yaml
script:
  breeze_tts_speak_direct:
    alias: "Breeze TTS Speak (Direct URL)"
    sequence:
      - service: rest_command.breeze_tts_warm
        response_variable: warm_response
      - service: media_player.play_media
        target:
          entity_id: "{{ media_player }}"
        data:
          media_content_id: "http://<tts-ip>:<tts-port>{{ warm_response.content.url }}"
          media_content_type: "music"

rest_command:
  breeze_tts_warm:
    url: "http://<tts-ip>:<tts-port>/v1/audio/speech/warm"
    method: POST
    headers:
      Content-Type: "application/x-www-form-urlencoded"
    payload: "text={{ text }}&instruction={{ instruction | default('A calm, friendly adult voice speaking clearly.') }}&cfg_scale={{ cfg_scale | default(1) }}&seed={{ seed | default(42) }}"
    content_type: "application/json"
```

> **Note:** The `rest_command` approach uses URL-encoded form data (not multipart), which the API also accepts. The response includes `{"id":"...","url":"/response/..."}`. Your media player streams the WAV directly from the TTS server.

## 5. Testing

After restarting HA with the new configuration:

### Test the health sensor

Go to **Developer Tools > States** and find `sensor.breeze_tts_status`. It should show `ok`.

### Test speech generation

Go to **Developer Tools > Services** and call:

```yaml
service: script.breeze_tts_speak
data:
  text: "Hello from Home Assistant! The text to speech integration is working."
  media_player: media_player.living_room
```

### Test from the command line (on the HA host)

```bash
# Check connectivity
curl -s http://<tts-ip>:<tts-port>/health

# Generate and download a test file
curl -s -X POST http://<tts-ip>:<tts-port>/v1/audio/speech/warm \
  -F "text=This is a test from Home Assistant." \
  -F "instruction=A calm, friendly adult voice." \
  -F "cfg_scale=1" \
  -F "seed=42"
# Note the "id" in the response, then:
curl -s http://<tts-ip>:<tts-port>/response/<id> -o test.wav
# Play test.wav to verify
```

## 6. Voice assistant pipeline (advanced)

To use Breeze TTS as the voice for HA's Assist (conversation) pipeline, you need a custom TTS platform component. Create `custom_components/breeze_tts/` in your HA config directory:

<details>
<summary>Custom component files (click to expand)</summary>

**`custom_components/breeze_tts/manifest.json`**

```json
{
  "domain": "breeze_tts",
  "name": "Breeze TTS",
  "version": "1.0.0",
  "documentation": "https://github.com/devdevgoat/agent-land-breeze-tts-api",
  "codeowners": [],
  "requirements": [],
  "iot_class": "local_polling"
}
```

**`custom_components/breeze_tts/__init__.py`**

```python
"""Breeze TTS integration."""
DOMAIN = "breeze_tts"

async def async_setup(hass, config):
    return True
```

**`custom_components/breeze_tts/tts.py`**

```python
"""Breeze TTS platform for Home Assistant."""
from __future__ import annotations

import logging
import struct
from io import BytesIO

import aiohttp

from homeassistant.components.tts import TextToSpeechEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CONF_HOST = "host"
CONF_PORT = "port"
CONF_INSTRUCTION = "instruction"
CONF_CFG_SCALE = "cfg_scale"

SAMPLE_RATE = 24000


def _pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw s16le PCM in a WAV header."""
    buf = BytesIO()
    num_samples = len(pcm) // 2
    data_size = num_samples * 2
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm)
    return buf.getvalue()


async def async_get_engine(hass, config, discovery_info=None):
    return BreezeTTSProvider(hass, config)


class BreezeTTSProvider(TextToSpeechEntity):
    """Breeze TTS speech provider."""

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        self.hass = hass
        self._host = config.get(CONF_HOST, "172.16.9.1")
        self._port = config.get(CONF_PORT, 7860)
        self._instruction = config.get(
            CONF_INSTRUCTION, "A calm, friendly adult voice speaking clearly."
        )
        self._cfg_scale = config.get(CONF_CFG_SCALE, 1.0)
        self._attr_name = "Breeze TTS"

    @property
    def default_language(self) -> str:
        return "en"

    @property
    def supported_languages(self) -> list[str]:
        return ["en", "zh"]

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict | None = None
    ) -> tuple[str, bytes]:
        url = f"http://{self._host}:{self._port}/v1/audio/speech"
        data = aiohttp.FormData()
        data.add_field("text", message)
        data.add_field("instruction", self._instruction)
        data.add_field("cfg_scale", str(self._cfg_scale))
        data.add_field("seed", "42")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        _LOGGER.error("Breeze TTS error: %s", resp.status)
                        return None, None
                    pcm = await resp.read()
                    return "wav", _pcm_to_wav(pcm)
        except Exception:
            _LOGGER.exception("Breeze TTS request failed")
            return None, None
```

</details>

Then add to `configuration.yaml`:

```yaml
tts:
  - platform: breeze_tts
    host: "<tts-ip>"
    port: 7860
    instruction: "A calm, friendly adult voice speaking clearly."
    cfg_scale: 1
```

After restarting HA, `tts.breeze_tts_say` is available as a service, and you can select "Breeze TTS" as the TTS engine in your Assist voice pipeline settings.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Health sensor shows `unavailable` | Check that HA can reach the TTS server: `curl http://<tts-ip>:<tts-port>/health` from the HA host |
| Shell command hangs or times out | The TTS server may be busy (one request at a time). Check server logs. Increase `wait_seconds` for long text |
| Media player doesn't play the file | Ensure `/config/www/` exists and the media player supports WAV. Try the direct URL approach |
| `409 Conflict` from the API | The server is already generating. The warm endpoint queues automatically; for direct speech, retry after a few seconds |
| Audio quality issues | Increase `cfg_scale` to `4` when using an instruction. Try different seeds for different voice characteristics |

## API quick reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Server status |
| `/v1/audio/speech` | POST (form) | Stream raw PCM (24 kHz s16le) |
| `/v1/audio/speech/warm` | POST (form) | Queue generation, get response URL |
| `/response/{id}` | GET | Download WAV |
| `/v1/voices` | GET | List saved voices |
| `/usage` | GET | Full API docs |

See [API.md](API.md) for the complete reference.
