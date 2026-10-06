# Breeze TTS API reference

Streaming text-to-speech server (Breeze TTS 2) with saved, reusable voices.

- **Base URL:** `http://<server-ip>:7860` (LAN) or `http://127.0.0.1:7860` (on the host)
- **Auth:** none. LAN only.
- **Concurrency:** one request at a time. A request made while another is generating gets `409`; retry after a short wait (about 0.5–1 s, with backoff).
- **Runtime:** served by the GGUF runtime (Breeze-TTS-2.cpp, Q8_0, container `breeze-tts-gguf`). The original PyTorch runtime implements the same API and can be switched back in; see the README. Differences between the two are listed in [Runtime differences](#runtime-differences).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/audio/speech` | Generate speech (streaming) |
| `GET` | `/v1/voices` | List saved voice ids |
| `GET` | `/v1/voices/{id}` | One voice's metadata |
| `GET` | `/health` | Server readiness |
| `POST` | `/v1/audio/speech/warm` | Start generating; returns an id at once (GGUF runtime) |
| `GET` | `/response/{id}` | Play a warm response as WAV (streams while generating) |
| `GET` | `/response/{id}/status` | Progress of a warm response |
| `GET` | `/usage` | This guide as Markdown |

## POST /v1/audio/speech

Request body is `multipart/form-data` (not JSON).

| Field | Type | Default | Meaning |
|---|---|---|---|
| `text` | string | required | What to say. English or Chinese. Inline vocal events: English `(laugh)`, `(sigh)`, `(cough)`, `(clears throat)`; Chinese `[笑]`, `[叹气]`, `[咳嗽]`, `[清嗓子]`. |
| `voice` | string | — | A saved voice id from `GET /v1/voices` (for example `narrator`, `v-bd5d3e9952f6`). |
| `instruction` | string | — | Natural-language description of the voice or delivery, for example `"A warm, calm young woman."` or `"Speak slowly with a serious tone."`. Match its language to `text`. |
| `cfg_scale` | float > 0 | `1.0` | How strongly the speech follows `instruction`. Use `4` when sending an instruction. Cloning without an instruction: use `1` (the PyTorch runtime returns `400` for anything else; the GGUF runtime accepts it and guides toward a neutral "Speak clearly and naturally."). |
| `seed` | int | `42` | Sampling seed. Part of a designed voice's identity (see below). |
| `ref_audio` | file | — | One-off reference clip to clone (clean speech, about 5–15 s). Any common audio format (WAV, MP3, M4A, ...). Requires `ref_text`. Not saved. |
| `ref_text` | string | — | Exact transcript of `ref_audio`. |

Do not combine `voice` with `ref_audio`/`ref_text` (returns `400`).

### Response

- `200`, body is a **stream of raw PCM**: signed 16-bit little-endian, mono, 24000 Hz. No WAV header. Chunks arrive as they are generated (first audio typically 350–800 ms; about 0.9 s for a one-off `ref_audio` upload), so you can play while receiving. The first chunk is 0.32 s of audio and chunks grow to 3.2 s, so buffer at least ~1 s before starting playback. An odd byte count can split a sample across chunks; carry the leftover byte into the next chunk.
- Headers:
  - `X-Sample-Rate: 24000`, `X-Sample-Format: s16le`
  - `X-Voice-Id`: the voice used or created (absent for one-off `ref_audio` and plain requests)
  - `X-Voice-Status`: `created` (new designed voice; saved when this stream finishes), `reused` (instruction + seed matched a saved voice), or `saved` (you passed `voice=`)

To save a playable file, write a WAV header (1 channel, 16-bit, 24000 Hz) and append the bytes.

### Errors

The body is JSON: `{"detail": "..."}`.

| Code | When |
|---|---|
| `400` | Invalid input: empty `text`; `cfg_scale` ≤ 0; `voice` together with `ref_audio`; `ref_audio` without `ref_text` (or the reverse); unreadable `ref_audio`; bad voice name. PyTorch runtime only: `cfg_scale` ≠ 1 when cloning without an instruction |
| `404` | Unknown `voice` (the message lists the available ids) |
| `409` | Busy with another request; retry |
| `500` | Server fault. On an engine crash or GPU fault the container restarts itself; `/health` returns errors or `503` for about 15–20 s (about 2 minutes on the PyTorch runtime), then it's back |
| `503` | The engine is still loading or restarting; wait and retry |

## Warm responses: get an id now, play it from a URL

For when you want the reply to start generating **before** you're ready to play it, or want a plain URL to hand to a player. Available on the GGUF runtime.

**1. `POST /v1/audio/speech/warm`**: same multipart fields as `/v1/audio/speech`, with the same validation errors. Returns about 30–40 ms later with `202`:

```json
{"id": "e9c268a4c01342e4", "url": "/response/e9c268a4c01342e4",
 "status_url": "/response/e9c268a4c01342e4/status",
 "state": "queued", "queue_position": 0, "estimated_seconds": 17.0}
```

- **Queueing:** unlike `/v1/audio/speech`, it never returns `409`. Warm requests queue for the GPU in order. `queue_position` is how many jobs are ahead of this one.
- **Pending limit:** at most 32 jobs can be pending; beyond that the server returns `503` with `Retry-After`.

**2. `GET /response/{id}`**: play it. Point any player at `http://<server-ip>:7860/response/{id}`.
- **Finished:** a normal `audio/wav` file with `Content-Length` and range requests, so players can seek.
- **Still generating:** a WAV stream that grows as audio is generated and ends when generation does. The header has "unknown length", which players handle.
- **Requested too early:** the server holds back the first audio byte until there's enough of a head start that playback shouldn't catch up with generation. The player just sees a slightly slower start (measured 1.2–2.3 s for a 50-word reply); no polling or retrying needed.
- **Queued behind another job:** the request waits until this job's audio is ready.
- **Options:** `?format=pcm` returns raw s16le PCM instead of WAV. `?min_lead=2.5` requests a bigger head start (seconds), for players with large or uneven buffering.
- **Headers:** `X-Voice-Id` and `X-Voice-Status`, as in `/v1/audio/speech`.
- **Errors:** `404` for an unknown or expired id, `500` if generation failed.
- **Engine restarts:** if the engine is restarting (for example after a GPU fault), warm jobs wait for it, up to 60 s (`BREEZE_ENGINE_WAIT_SECONDS`), instead of failing.

**3. `GET /response/{id}/status`**:

```json
{"id": "e9c268a4c01342e4", "state": "done", "url": "/response/e9c268a4c01342e4",
 "generated_seconds": 16.0, "estimated_seconds": 17.0, "duration_seconds": 16.0,
 "realtime_factor": 0.89, "queued_seconds": 0.01, "error": null,
 "x-voice-id": "narrator", "x-voice-status": "saved"}
```

`state` is one of `queued`, `generating`, `done` or `error`.

**Lifetime:** responses are kept for **24 hours** (`BREEZE_RESPONSE_TTL_SECONDS`) in `responses/` and survive a server restart. A job that was mid-generation during a restart is dropped.

**How the head start is chosen:** generation runs only slightly faster than real time (real-time factor about 0.85–0.95), and the engine delivers audio in pieces of up to about 1 s (`--chunk-max 12`). Before releasing audio, the server waits until it has at least one piece's worth at the current speed, and enough that generation is predicted to finish at least 1 s before playback reaches the end. In tests, a player starting immediately never had less than 0.77 s of audio in hand.

```bash
ID=$(curl -s -X POST http://<server-ip>:7860/v1/audio/speech/warm -F voice=narrator -F "text=Deploy finished, all green." | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
ffplay -nodisp -autoexit "http://<server-ip>:7860/response/$ID"      # or open the URL in any player
```

## Voices: create once, reuse anywhere

There are three ways to choose a voice:

1. **Design a new voice:** send `instruction` (+ `cfg_scale=4`, + a `seed`) and no `voice`. The first time an instruction + seed pair is seen, the response has `X-Voice-Status: created` and `X-Voice-Id: v-<12 hex>`. When the stream finishes, the server saves that audio and text as the voice's reference clip.
   - It only saves if the first clip is 1–30 s long. Keep the first text to one or two sentences, about 5–15 s of speech, for the best reference.
   - The voice is saved only if your client reads the whole stream. If you disconnect early, nothing is saved.
2. **Reuse by instruction + seed:** send the same `instruction` and `seed` again with any new text. You get `X-Voice-Status: reused` and the same id. The server clones from the saved clip, so the voice stays consistent. Instruction + seed alone would drift as soon as the text changes. The instruction must match exactly (leading and trailing spaces are ignored).
3. **Reuse by id:** send `voice=<id>`. A designed voice automatically reapplies its stored instruction. You can send a different `instruction` to change the delivery for that request (for example `"Whisper."`), and the voice identity is kept.

The id is deterministic: `v-` + the first 12 hex characters of `sha256(instruction + "\0" + str(seed))`. A client can compute it ahead of time and store it. To get a different voice from the same description, change the `seed`.

Hand-made voices (for example `narrator`, `narrator-calm`) are reference clips placed on the server. Use them with `voice=<name>`. They have no stored instruction, so either send `cfg_scale=1`, or add an `instruction` together with `cfg_scale=4`.

The GGUF runtime encodes each voice's reference clip once and keeps it in memory, so the first use of a voice after a restart takes about 0.3–0.7 s longer. Hand-made voices are encoded at startup.

### Recommended client flow

```text
1. GET /v1/voices once at startup (cache it).
2. New character: POST {text: <short first line>, instruction, cfg_scale: 4, seed}
     -> read X-Voice-Id, store it with the character, and read the full stream.
3. Every later line: POST {text, voice: <stored id>, cfg_scale: 4}
     (add an instruction only to change the delivery for that line)
4. On 409: retry with backoff. On 503 or a connection error: wait and retry (server may be restarting).
```

## GET /v1/voices

```json
{"voices": ["narrator", "narrator-calm", "v-bd5d3e9952f6"]}
```

## GET /v1/voices/{id}

```json
{"id": "v-bd5d3e9952f6",
 "ref_text": "Ahoy there. The sea has been rough tonight, but we will make port by morning.",
 "instruction": "A deep, gravelly old sea captain with a slow, weathered drawl.",
 "seed": 7, "cfg_scale": 4.0, "seconds": 6.56, "created": 1791222610.26}
```

Hand-made voices return only `id` and `ref_text`.

## GET /health

`200 {"status":"ok","sample_rate":24000}` when ready; `503 {"status":"loading"}` while starting (about 15 s after a restart on the GGUF runtime, about 2 minutes on PyTorch).

## Examples

curl, designing a voice and capturing the id:

```bash
curl -sS -D headers.txt -X POST http://<server-ip>:7860/v1/audio/speech \
  -F "text=Ahoy there. The sea has been rough tonight." \
  -F "instruction=A deep, gravelly old sea captain with a slow, weathered drawl." \
  -F "cfg_scale=4" -F "seed=7" --output line1.pcm
grep -i x-voice-id headers.txt

# reuse it
curl -sS -X POST http://<server-ip>:7860/v1/audio/speech \
  -F "voice=v-bd5d3e9952f6" -F "cfg_scale=4" -F "text=Hoist the sails!" --output line2.pcm

# play live (ffmpeg)
curl -sN -X POST http://<server-ip>:7860/v1/audio/speech -F "voice=narrator" -F "text=Hey!" \
  | ffplay -f s16le -ar 24000 -ch_layout mono -nodisp -autoexit -
```

Python, streaming to a WAV file:

```python
import requests, wave

def speak(text, out_path, *, voice=None, instruction=None, cfg_scale=None, seed=42,
          base="http://<server-ip>:7860"):
    data = {"text": text, "seed": str(seed)}
    if voice:
        data["voice"] = voice
    if instruction:
        data["instruction"] = instruction
    # cfg 4 when there's an instruction (or a designed voice that carries one); else 1
    data["cfg_scale"] = str(cfg_scale if cfg_scale is not None else (4 if instruction else 1))
    with requests.post(f"{base}/v1/audio/speech", data=data, stream=True, timeout=120) as r:
        r.raise_for_status()
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2)
            w.setframerate(int(r.headers.get("X-Sample-Rate", 24000)))
            left = b""
            for chunk in r.iter_content(chunk_size=None):
                chunk = left + chunk
                cut = len(chunk) - len(chunk) % 2
                w.writeframes(chunk[:cut]); left = chunk[cut:]
        return r.headers.get("X-Voice-Id"), r.headers.get("X-Voice-Status")

vid, status = speak("Ahoy there.", "a.wav",
                    instruction="A deep, gravelly old sea captain.", seed=7)   # -> created
speak("Hoist the sails!", "b.wav", voice=vid, cfg_scale=4)                     # same voice
```

## Limits and tips

- Speed (GGUF runtime): generation runs about 1.0–1.25× faster than real time on the 4090 (RTF 0.8–1.0), less while the image-generation service is busy on the same GPU. That is a thin margin: buffer 1–2 s before playing, or download the whole clip for long text. (The PyTorch runtime runs about 1.5× real time.)
- Very long text works, but you get audio sooner and recover from errors better if you send one or two sentences per request. The GGUF runtime splits text longer than ~600 characters at sentence boundaries and generates the pieces in sequence in the same voice.
- Generation stops at about 1,500 audio frames (about 2 minutes) per request (per ~600-character piece on the GGUF runtime).
- `cfg_scale` above ~3 can add some harshness on the GGUF runtime; `4` still works and is what existing clients send.
- Reference clips should be clean speech without music or noise. The transcript must match the audio exactly.
- License: Breeze TTS 2 weights and self-hosted outputs are for research and non-commercial use only.

## Runtime differences

The GGUF runtime (Breeze-TTS-2.cpp) keeps every field, header, status code and voice file of the PyTorch runtime. What differs:

| | PyTorch runtime | GGUF runtime |
|---|---|---|
| `cfg_scale` ≠ 1 when cloning with no instruction | `400` | Accepted. The engine always has an instruction; with none given it uses "Speak clearly and naturally." |
| Audio | Reference implementation | Same model, Q8_0 weights; not bit-identical, so a given seed sounds different than it did on PyTorch. Saved voices are still cloned from the same clips |
| First audio | ~100–500 ms | ~350–800 ms (saved or designed voice), ~0.9 s (one-off upload) |
| Speed (RTF) | ~0.66 | ~0.8–1.0 |
| GPU memory | ~17–18 GB | ~4–5 GB, peak ~5.3 GB |
| Long text | One pass | Split at ~600 characters on sentence boundaries |
| `ref_audio` formats | WAV (and whatever the upstream loader reads) | Anything ffmpeg reads; converted to 24 kHz mono |
| Restart after a crash | ~2 min | ~15–20 s |
