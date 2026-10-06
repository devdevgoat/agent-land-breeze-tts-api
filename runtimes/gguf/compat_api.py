"""breeze-tts compatibility API on top of the Breeze-TTS-2.cpp (GGUF) server.

Implements the contract documented in ../../API.md (the same one runtimes/pytorch/voice_api.py
serves for the PyTorch runtime) and forwards generation to ``breeze-server``,
which runs on 127.0.0.1 inside the same container.

  POST /v1/audio/speech   multipart: text, voice, instruction, cfg_scale, seed,
                          ref_audio (file) + ref_text -> streaming s16le 24 kHz PCM
  GET  /v1/voices         {"voices": [names]}
  GET  /v1/voices/{id}    metadata
  GET  /health            {"status":"ok","sample_rate":24000}, 503 while loading
  POST /v1/audio/speech/warm   same fields -> 202 {"id", "url"} at once; generates in the background
  GET  /response/{id}     plays it (WAV): streams while generating, a plain file when done
  GET  /response/{id}/status   progress
  GET  /usage             this API's guide (API.md) as Markdown

Voices are the same files as before (BREEZE_VOICES_DIR, default /voices):
<id>.wav + <id>.txt (+ <id>.json). The engine encodes a reference clip once and
keeps it in memory under a hash id; this layer registers each saved voice on
first use (POST /v1/voices on the engine) and reuses that id, re-registering if
the engine restarted or evicted it, or if the files changed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import re
import struct
import subprocess
import time
import wave
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

ENGINE = os.environ.get("BREEZE_ENGINE_URL", "http://127.0.0.1:8080")
VOICES_DIR = Path(os.environ.get("BREEZE_VOICES_DIR", "/voices"))
VOICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SAMPLE_RATE = 24000
DEFAULT_CFG_SCALE = 1.0
# A designed voice is only saved if its first clip makes a usable reference.
MIN_SAVE_SECONDS = 1.0
MAX_SAVE_SECONDS = 30.0
# Frame cap per text piece (12.5 frames/s). The PyTorch service stopped at ~1500.
MAX_NEW_TOKENS = int(os.environ.get("BREEZE_MAX_NEW_TOKENS", "1500"))

app = FastAPI(title="Breeze TTS (GGUF)")
client = httpx.AsyncClient(base_url=ENGINE, timeout=httpx.Timeout(10.0, read=600.0))
busy = asyncio.Lock()
# voice name -> (wav mtime, wav size, txt mtime, engine voice id)
engine_voice_ids: dict[str, tuple[float, int, float, str]] = {}


# ---------------------------------------------------------------- voice files
# Same rules and file layout as runtimes/pytorch/voice_api.py.

def _list_voices() -> list[str]:
    if not VOICES_DIR.is_dir():
        return []
    return sorted(
        wav.stem
        for wav in VOICES_DIR.glob("*.wav")
        if VOICE_NAME.match(wav.stem) and wav.with_suffix(".txt").is_file()
    )


def _metadata(name: str) -> dict:
    path = VOICES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _resolve_voice(name: str) -> tuple[Path, str, dict]:
    if not VOICE_NAME.match(name):
        raise HTTPException(status_code=400, detail="Invalid voice name.")
    wav = VOICES_DIR / f"{name}.wav"
    txt = wav.with_suffix(".txt")
    if not (wav.is_file() and txt.is_file()):
        raise HTTPException(
            status_code=404,
            detail=f"Unknown voice '{name}'. Available: {', '.join(_list_voices()) or 'none'}.",
        )
    ref_text = txt.read_text(encoding="utf-8").strip()
    if not ref_text:
        raise HTTPException(status_code=500, detail=f"Transcript for '{name}' is empty.")
    return wav, ref_text, _metadata(name)


def _designed_voice_id(instruction: str, seed: int) -> str:
    digest = hashlib.sha256(f"{instruction}\x00{seed}".encode("utf-8")).hexdigest()
    return f"v-{digest[:12]}"


def _save_designed_voice(voice_id: str, pcm: bytes, text: str, meta: dict) -> None:
    seconds = len(pcm) / 2 / SAMPLE_RATE
    if not MIN_SAVE_SECONDS <= seconds <= MAX_SAVE_SECONDS:
        print(
            f"voice {voice_id} not saved: first clip is {seconds:.1f}s "
            f"(needs {MIN_SAVE_SECONDS:.0f}-{MAX_SAVE_SECONDS:.0f}s)",
            flush=True,
        )
        return
    wav = VOICES_DIR / f"{voice_id}.wav"
    # Write the wav last and rename into place, so a voice is only listed once
    # its audio and transcript are both complete.
    tmp = wav.with_suffix(".wav.tmp")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    meta = {**meta, "id": voice_id, "seconds": round(seconds, 2), "created": time.time()}
    (VOICES_DIR / f"{voice_id}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (VOICES_DIR / f"{voice_id}.txt").write_text(text, encoding="utf-8")
    tmp.replace(wav)
    print(f"voice {voice_id} saved: {json.dumps(meta, ensure_ascii=False)}", flush=True)


def _to_wav(data: bytes) -> bytes:
    """Any uploaded audio -> clean 24 kHz mono 16-bit WAV.

    The engine only reads PCM/float WAV and does not bounds-check the chunk
    sizes, so a WAV with bogus sizes (e.g. ffmpeg writing to a pipe leaves them
    at 0xFFFFFFFF) crashes it. ffmpeg decodes to raw PCM and the header is
    written here with the real sizes.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-f", "s16le", "-c:a", "pcm_s16le", "pipe:1"],
            input=data, capture_output=True, timeout=60, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=400, detail="ref_audio is not a readable audio file.") from error
    pcm = proc.stdout[: len(proc.stdout) // 2 * 2]
    if len(pcm) < SAMPLE_RATE // 2:  # under ~0.25 s
        raise HTTPException(status_code=400, detail="ref_audio is empty or too short.")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------- engine calls

async def _register_voice(name: str, wav: Path, ref_text: str) -> str:
    """Engine voice id for a saved voice, encoding it on the engine if needed."""
    stat, txt_stat = wav.stat(), wav.with_suffix(".txt").stat()
    cached = engine_voice_ids.get(name)
    if cached and cached[:3] == (stat.st_mtime, stat.st_size, txt_stat.st_mtime):
        return cached[3]
    try:
        r = await client.post(
            "/v1/voices",
            files={"ref_audio": (wav.name, _to_wav(wav.read_bytes()), "audio/wav")},
            data={"ref_text": ref_text},
        )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail=f"TTS engine unavailable: {error!r}") from error
    if r.status_code == 409:
        raise HTTPException(status_code=409, detail="An inference request is already running.")
    if r.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Engine could not encode voice '{name}': {r.text}")
    engine_id = r.json()["id"]
    engine_voice_ids[name] = (stat.st_mtime, stat.st_size, txt_stat.st_mtime, engine_id)
    print(f"voice {name} registered with engine as {engine_id} ({r.json().get('encode_ms', 0):.0f} ms)", flush=True)
    return engine_id


async def _open_stream(data: dict, files: dict | None) -> httpx.Response:
    request = client.build_request("POST", "/v1/audio/speech", data=data, files=files)
    try:
        response = await client.send(request, stream=True)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail=f"TTS engine unavailable: {error!r}") from error
    if response.status_code != 200:
        body = (await response.aread()).decode("utf-8", "replace")
        await response.aclose()
        try:
            message = json.loads(body).get("error", body)
        except ValueError:
            message = body
        code = response.status_code if response.status_code in (400, 404, 409) else 500
        raise HTTPException(status_code=code, detail=f"Engine: {message}")
    return response


class _Relay:
    """Streams the engine response to the client and owns the `busy` lock.

    The lock is released exactly once: when the stream ends, fails, or the
    client disconnects, or (if the body was never even started) when this
    object is garbage-collected.
    """

    def __init__(self, response: httpx.Response, on_complete: Callable[[bytes], None] | None):
        self.response = response
        self.on_complete = on_complete
        self.released = False

    def _release(self) -> None:
        if not self.released:
            self.released = True
            busy.release()

    def __del__(self) -> None:
        if not self.released:
            self._release()
            try:
                asyncio.get_running_loop().create_task(self.response.aclose())
            except RuntimeError:
                pass

    async def __aiter__(self) -> AsyncIterator[bytes]:
        pcm = bytearray() if self.on_complete else None
        completed = False
        try:
            async for chunk in self.response.aiter_raw():
                if pcm is not None:
                    pcm += chunk
                yield chunk
            completed = True
        except httpx.HTTPError as error:
            print(f"engine stream broke: {error!r}", flush=True)
        finally:
            # Closing the engine connection early (client went away) makes the
            # engine's next write fail, which stops its generation and frees the GPU.
            await self.response.aclose()
            self._release()
        # Only reached when generation finished and the client read everything; a
        # disconnect stops at `yield`, so a partial clip is never saved.
        if completed and self.on_complete:
            try:
                self.on_complete(bytes(pcm))
            except Exception as error:  # saving must never break a finished response
                print(f"saving voice failed: {error!r}", flush=True)


async def _speech(
    text: str,
    instruction: str | None,
    cfg_scale: float,
    seed: int,
    *,
    voice: str | None = None,
    upload: tuple[bytes, str] | None = None,
    on_complete: Callable[[bytes], None] | None = None,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Run one generation on the engine; the caller already holds `busy`."""
    data = {
        "text": text,
        "cfg_scale": repr(float(cfg_scale)),
        "seed": str(seed),
        "max_new_tokens": str(MAX_NEW_TOKENS),
    }
    if instruction:
        data["instruction"] = instruction
    files = None
    if upload is not None:
        files = {"ref_audio": ("ref.wav", _to_wav(upload[0]), "audio/wav")}
        data["ref_text"] = upload[1]
    if voice is not None:
        wav, ref_text, _ = _resolve_voice(voice)
        data["voice_id"] = await _register_voice(voice, wav, ref_text)
        try:
            response = await _open_stream(data, files)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            # The engine forgot the voice (restart or eviction): encode it again.
            engine_voice_ids.pop(voice, None)
            data["voice_id"] = await _register_voice(voice, wav, ref_text)
            response = await _open_stream(data, files)
    else:
        response = await _open_stream(data, files)
    out_headers = {"X-Sample-Rate": str(SAMPLE_RATE), "X-Sample-Format": "s16le", "Cache-Control": "no-store"}
    out_headers.update(headers or {})
    return StreamingResponse(_Relay(response, on_complete), media_type="audio/pcm", headers=out_headers)


async def _warm_voices() -> None:
    """Once the engine is up, encode the hand-made voices (no .json, e.g. narrator) so
    the first request for them after a restart does not pay the encode."""
    while True:
        try:
            if (await client.get("/health", timeout=5.0)).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        await asyncio.sleep(2)
    for name in _list_voices():
        if (VOICES_DIR / f"{name}.json").is_file():
            continue
        async with busy:
            try:
                wav, ref_text, _ = _resolve_voice(name)
                await _register_voice(name, wav, ref_text)
            except Exception as error:
                print(f"warming voice {name} failed: {error!r}", flush=True)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.get_running_loop().create_task(_warm_voices())


# ---------------------------------------------------------------- routes

USAGE_PATH = Path(os.environ.get("BREEZE_USAGE_PATH", "/opt/breeze/API.md"))


@app.get("/usage", response_class=PlainTextResponse)
def usage() -> PlainTextResponse:
    """The API guide (Markdown), mounted from the repo's API.md, so clients can read it."""
    if not USAGE_PATH.is_file():
        raise HTTPException(status_code=404, detail="API guide not mounted.")
    return PlainTextResponse(USAGE_PATH.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@app.get("/health")
async def health() -> JSONResponse:
    try:
        r = await client.get("/health", timeout=5.0)
        if r.status_code == 200:
            return JSONResponse({"status": "ok", "sample_rate": SAMPLE_RATE})
    except httpx.HTTPError:
        pass
    return JSONResponse({"status": "loading"}, status_code=503)


@app.get("/v1/voices")
def voices() -> dict[str, list[str]]:
    return {"voices": _list_voices()}


@app.get("/v1/voices/{name}")
def voice_info(name: str) -> dict:
    _, ref_text, meta = _resolve_voice(name)
    return {"id": name, "ref_text": ref_text, **meta}


@dataclass
class SpeechArgs:
    text: str
    voice: str | None
    instruction: str | None
    cfg_scale: float
    seed: int
    upload: tuple[bytes, str] | None


async def _validated(
    text: str, voice: str | None, instruction: str | None, cfg_scale: float,
    ref_audio: UploadFile | None, ref_text: str, seed: int, kind: str,
) -> SpeechArgs:
    """Request checks shared by /v1/audio/speech and /v1/audio/speech/warm."""
    has_upload = ref_audio is not None and bool(ref_audio.filename)
    instruction = instruction.strip() if instruction else None
    voice = voice.strip() if voice else None
    print(
        f"{kind} request: voice={voice or ('upload' if has_upload else '-')} "
        f"cfg={cfg_scale} seed={seed} instruction={instruction!r} "
        f"text={len(text)} chars: {text[:200]!r}",
        flush=True,
    )
    if not text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty.")
    if not math.isfinite(cfg_scale) or cfg_scale <= 0:
        raise HTTPException(status_code=400, detail="cfg_scale must be greater than 0.")
    if voice and (has_upload or ref_text.strip()):
        raise HTTPException(status_code=400, detail="Use either voice or ref_audio/ref_text, not both.")
    if has_upload != bool(ref_text.strip()):
        raise HTTPException(status_code=400, detail="ref_audio and ref_text must be provided together.")
    if voice:
        _resolve_voice(voice)  # 400/404 before taking the lock
    upload = (await ref_audio.read(), ref_text.strip()) if has_upload else None
    return SpeechArgs(text, voice, instruction, cfg_scale, seed, upload)


@app.post("/v1/audio/speech")
async def speech(
    text: str = Form(...),
    voice: str | None = Form(None),
    instruction: str | None = Form(None),
    cfg_scale: float = Form(DEFAULT_CFG_SCALE),
    ref_audio: UploadFile | None = File(None),
    ref_text: str = Form(""),
    seed: int = Form(42),
) -> StreamingResponse:
    args = await _validated(text, voice, instruction, cfg_scale, ref_audio, ref_text, seed, "speech")
    if busy.locked():
        raise HTTPException(status_code=409, detail="An inference request is already running.")
    await busy.acquire()
    return await _start(args)


async def _start(args: SpeechArgs) -> StreamingResponse:
    """Start a generation; the caller has acquired `busy`. The returned stream
    releases it when it ends; on failure here it is released immediately."""
    text, voice, instruction, cfg_scale, seed, upload = (
        args.text, args.voice, args.instruction, args.cfg_scale, args.seed, args.upload,
    )
    try:
        if upload is not None:
            return await _speech(text, instruction, cfg_scale, seed, upload=upload)

        status = "saved"
        if not voice and instruction:
            # Voice design: reuse the voice saved for this instruction + seed, if any.
            voice = _designed_voice_id(instruction, seed)
            if not (VOICES_DIR / f"{voice}.wav").is_file():
                meta = {"instruction": instruction, "seed": seed, "cfg_scale": cfg_scale}
                new_id = voice
                return await _speech(
                    text, instruction, cfg_scale, seed,
                    on_complete=lambda pcm: _save_designed_voice(new_id, pcm, text, meta),
                    headers={"X-Voice-Id": voice, "X-Voice-Status": "created"},
                )
            status = "reused"
        if not voice:
            return await _speech(text, instruction, cfg_scale, seed)

        _, _, meta = _resolve_voice(voice)
        # A designed voice keeps its describing instruction unless the request overrides it.
        instruction = instruction or meta.get("instruction")
        return await _speech(
            text, instruction, cfg_scale, seed, voice=voice,
            headers={"X-Voice-Id": voice, "X-Voice-Status": status},
        )
    except BaseException:
        busy.release()  # on success the stream releases it when it ends
        raise


# ---------------------------------------------------------------- warm responses
# POST /v1/audio/speech/warm returns an id at once and generates in the background
# into a WAV file; GET /response/{id} plays it, following the file while it grows.
# Generation runs at ~0.85x real time (RTF), only slightly ahead of playback, so the
# player is held back until enough audio exists that it shouldn't catch up.

RESPONSES_DIR = Path(os.environ.get("BREEZE_RESPONSES_DIR", "/responses"))
RESPONSE_TTL = int(os.environ.get("BREEZE_RESPONSE_TTL_SECONDS", str(24 * 3600)))
MAX_PENDING = int(os.environ.get("BREEZE_MAX_PENDING", "32"))
ENGINE_WAIT_SECONDS = int(os.environ.get("BREEZE_ENGINE_WAIT_SECONDS", "60"))
MIN_LEAD_SECONDS = float(os.environ.get("BREEZE_MIN_LEAD_SECONDS", "0.75"))
SECONDS_PER_WORD = 0.32  # default speaking rate (~0.30 s/word measured) when the voice's own is unknown
LENGTH_MARGIN = 1.1  # estimates err long: an underestimate shrinks the head start
SAFETY_SECONDS = 1.0  # spare audio the player should still have when generation ends...
SAFETY_FRACTION = 0.08  # ...or this share of the reply, whichever is larger


def _voice_seconds_per_word(voice: str | None) -> float | None:
    """Speaking rate of a saved voice, from its reference clip and transcript. Voices
    differ a lot (a calm voice measured ~0.37 s/word vs ~0.30 default); a short
    reference (< 4 words) says little, so it isn't trusted."""
    if not voice or not VOICE_NAME.match(voice):
        return None
    wav, txt = VOICES_DIR / f"{voice}.wav", VOICES_DIR / f"{voice}.txt"
    try:
        words = len(txt.read_text(encoding="utf-8").split())
        with wave.open(str(wav), "rb") as f:
            seconds = f.getnframes() / f.getframerate()
    except (OSError, wave.Error, EOFError):
        return None
    if words < 4:
        return None
    return min(max(seconds / words, 0.2), 0.8)


def _chunk_max_seconds() -> float:
    """Largest piece the engine delivers at once (--chunk-max frames at 12.5 frames/s).
    Audio arrives in growing chunks up to this size, each taking about as long to
    generate as it lasts, so the player needs at least one chunk in hand."""
    match = re.search(r"--chunk-max\s+(\d+)", os.environ.get("BREEZE_ENGINE_ARGS", ""))
    return int(match.group(1)) / 12.5 if match else 3.2


CHUNK_MAX_SECONDS = _chunk_max_seconds()
BYTES_PER_SECOND = SAMPLE_RATE * 2
WAV_HEADER = 44


def _wav_header(pcm_bytes: int | None) -> bytes:
    """44-byte PCM WAV header; None = length unknown (streaming), as players expect."""
    data = 0xFFFFFFFF if pcm_bytes is None else pcm_bytes
    riff = 0xFFFFFFFF if pcm_bytes is None else 36 + pcm_bytes
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", riff, b"WAVE", b"fmt ", 16, 1, 1,
        SAMPLE_RATE, BYTES_PER_SECOND, 2, 16, b"data", data,
    )


@dataclass
class WarmJob:
    id: str
    path: Path
    estimated_seconds: float
    created: float = field(default_factory=time.time)
    state: str = "queued"  # queued | generating | done | error
    started: float | None = None
    finished: float | None = None
    pcm_bytes: int = 0
    error: str | None = None
    voice_headers: dict[str, str] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def notify(self) -> None:
        # Waiters hold the old event; set it and hand out a fresh one.
        event, self.changed = self.changed, asyncio.Event()
        event.set()

    @property
    def generated_seconds(self) -> float:
        return self.pcm_bytes / BYTES_PER_SECOND

    def rate(self) -> float | None:
        """Audio seconds produced per wall second (>1 = faster than real time).
        Counted from the start, including time to first audio, so it starts low
        (conservative) and settles as generation goes on."""
        if not self.started or not self.pcm_bytes:
            return None
        elapsed = (self.finished or time.time()) - self.started
        return self.generated_seconds / elapsed if elapsed > 0 else None

    def lead_needed(self, min_lead: float) -> float:
        """Seconds of audio to have before playback starts so it won't catch up.
        Two constraints: (1) bridge the wait for the next chunk, which can be a
        full CHUNK_MAX_SECONDS and takes ~that long / rate to arrive; (2) finish
        in time: with total T, produced g and rate r, generation ends after
        (T - g) / r and playback after T, so g >= T - r * (T - safety)."""
        rate = self.rate()
        if rate is None:
            return float("inf")
        total = max(self.estimated_seconds, self.generated_seconds * 1.1)
        next_chunk = CHUNK_MAX_SECONDS / rate + 0.25
        safety = max(SAFETY_SECONDS, SAFETY_FRACTION * total)
        finish = total - rate * (total - safety)
        return max(min_lead, next_chunk, finish)

    def status(self) -> dict:
        rate = self.rate()
        return {
            "id": self.id,
            "state": self.state,
            "url": f"/response/{self.id}",
            "generated_seconds": round(self.generated_seconds, 2),
            "estimated_seconds": round(self.estimated_seconds, 1),
            "duration_seconds": round(self.generated_seconds, 2) if self.state == "done" else None,
            "realtime_factor": round(1 / rate, 2) if rate else None,
            "queued_seconds": round((self.started or time.time()) - self.created, 2),
            "error": self.error,
            **{k.lower(): v for k, v in self.voice_headers.items()},
        }


jobs: dict[str, WarmJob] = {}


async def _engine_ready(timeout: float) -> bool:
    """Wait for breeze-server to answer /health (e.g. while it restarts after a crash)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if (await client.get("/health", timeout=3.0)).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(1)


async def _run_warm(job: WarmJob, args: SpeechArgs) -> None:
    try:
        # A warm job has no caller waiting on it, so ride out an engine restart
        # (crash recovery takes ~10-20 s) instead of failing: wait for the engine,
        # and if it drops while starting, wait and try once more.
        for attempt in (1, 2):
            await busy.acquire()  # queue behind other work (FIFO), no 409 for warm requests
            if not await _engine_ready(ENGINE_WAIT_SECONDS):
                busy.release()
                raise HTTPException(status_code=503, detail="TTS engine unavailable (did not come back in time).")
            if attempt == 1:  # start the clock only now: the rate drives the playback head start
                job.state, job.started = "generating", time.time()
                job.notify()
            try:
                response = await _start(args)  # releases `busy` itself when the stream ends, or on failure
                break
            except HTTPException as error:
                if error.status_code != 503 or attempt == 2:
                    raise
        job.voice_headers = {k: v for k, v in response.headers.items() if k.lower().startswith("x-voice")}
        with job.path.open("r+b") as f:
            f.seek(WAV_HEADER)
            async for chunk in response.body_iterator:
                f.write(chunk)
                f.flush()
                job.pcm_bytes += len(chunk)
                job.notify()
            f.seek(0)
            f.write(_wav_header(job.pcm_bytes))
        job.state = "done"
    except Exception as error:  # noqa: BLE001 - reported through /status
        job.state = "error"
        job.error = error.detail if isinstance(error, HTTPException) else repr(error)
    finally:
        job.finished = time.time()
        job.notify()
        print(
            f"warm {job.id}: {job.state}, {job.generated_seconds:.1f}s audio, "
            f"queued {job.status()['queued_seconds']}s, rtf {job.status()['realtime_factor']}"
            + (f", error {job.error}" if job.error else ""),
            flush=True,
        )


async def _cleanup_responses() -> None:
    while True:
        await asyncio.sleep(60)
        cutoff = time.time() - RESPONSE_TTL
        for job_id, job in list(jobs.items()):
            if job.finished and job.finished < cutoff:
                jobs.pop(job_id, None)
                job.path.unlink(missing_ok=True)


@app.on_event("startup")
async def _startup_responses() -> None:
    """Finished responses survive a restart (their links keep working until they
    expire); ones that were mid-generation are incomplete, so they're dropped."""
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - RESPONSE_TTL
    for path in RESPONSES_DIR.glob("*.wav"):
        with path.open("rb") as f:
            header = f.read(WAV_HEADER)
        data_size = struct.unpack("<I", header[40:44])[0] if len(header) == WAV_HEADER else 0xFFFFFFFF
        mtime = path.stat().st_mtime
        if data_size == 0xFFFFFFFF or mtime < cutoff:
            path.unlink(missing_ok=True)
            continue
        job = WarmJob(id=path.stem, path=path, estimated_seconds=data_size / BYTES_PER_SECOND,
                      created=mtime, state="done", started=mtime, finished=mtime, pcm_bytes=data_size)
        jobs[job.id] = job
    asyncio.get_running_loop().create_task(_cleanup_responses())


def _job(job_id: str) -> WarmJob:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired response id.")
    return job


@app.post("/v1/audio/speech/warm", status_code=202)
async def speech_warm(
    text: str = Form(...),
    voice: str | None = Form(None),
    instruction: str | None = Form(None),
    cfg_scale: float = Form(DEFAULT_CFG_SCALE),
    ref_audio: UploadFile | None = File(None),
    ref_text: str = Form(""),
    seed: int = Form(42),
) -> JSONResponse:
    args = await _validated(text, voice, instruction, cfg_scale, ref_audio, ref_text, seed, "warm")
    pending = sum(1 for j in jobs.values() if j.state in ("queued", "generating"))
    if pending >= MAX_PENDING:
        raise HTTPException(status_code=503, detail="Too many responses pending.", headers={"Retry-After": "5"})
    job_id = uuid.uuid4().hex[:16]
    job = WarmJob(
        id=job_id,
        path=RESPONSES_DIR / f"{job_id}.wav",
        estimated_seconds=max(1.0, len(text.split()) * LENGTH_MARGIN * (
            _voice_seconds_per_word(args.voice or (
                _designed_voice_id(args.instruction, args.seed) if args.instruction and not args.upload else None
            )) or SECONDS_PER_WORD)),
    )
    job.path.write_bytes(_wav_header(None))
    jobs[job_id] = job
    job.task = asyncio.get_running_loop().create_task(_run_warm(job, args))
    return JSONResponse(
        {"id": job_id, "url": f"/response/{job_id}", "status_url": f"/response/{job_id}/status",
         "state": job.state, "queue_position": pending, "estimated_seconds": round(job.estimated_seconds, 1)},
        status_code=202,
    )


@app.get("/response/{job_id}/status")
def response_status(job_id: str) -> dict:
    return _job(job_id).status()


async def _follow(job: WarmJob, wav: bool) -> AsyncIterator[bytes]:
    """Stream the file as it grows, until generation ends."""
    if wav:
        yield _wav_header(None)
    position = WAV_HEADER
    with job.path.open("rb") as f:
        while True:
            changed = job.changed  # take before checking, so no update is missed
            end = WAV_HEADER + job.pcm_bytes
            if position < end:
                f.seek(position)
                data = f.read(end - position)
                position += len(data)
                yield data
                continue
            if job.state in ("done", "error"):
                return
            try:
                await asyncio.wait_for(changed.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass


@app.get("/response/{job_id}", response_model=None)
async def response_audio(job_id: str, format: str = "wav", min_lead: float = MIN_LEAD_SECONDS):
    """Play a warm response. Finished: a normal file (Content-Length, seeking).
    Still generating: waits for a safe head start, then streams as it is written."""
    if format not in ("wav", "pcm"):
        raise HTTPException(status_code=400, detail="format must be wav or pcm.")
    job = _job(job_id)
    while job.state in ("queued", "generating"):
        changed = job.changed
        if job.state == "generating" and job.generated_seconds >= job.lead_needed(min_lead):
            break
        try:
            await asyncio.wait_for(changed.wait(), timeout=0.5)  # also re-checks as the rate settles
        except asyncio.TimeoutError:
            pass
    if job.state == "error":
        raise HTTPException(status_code=500, detail=f"Generation failed: {job.error}")
    headers = {"Cache-Control": "no-store", **job.voice_headers}
    if job.state == "done":
        if format == "wav":
            return FileResponse(job.path, media_type="audio/wav", headers=headers)
        return StreamingResponse(_follow(job, wav=False), media_type="audio/pcm",
                                 headers={**headers, "X-Sample-Rate": str(SAMPLE_RATE), "X-Sample-Format": "s16le"})
    if format == "wav":
        return StreamingResponse(_follow(job, wav=True), media_type="audio/wav", headers=headers)
    return StreamingResponse(_follow(job, wav=False), media_type="audio/pcm",
                             headers={**headers, "X-Sample-Rate": str(SAMPLE_RATE), "X-Sample-Format": "s16le"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "7860")))
