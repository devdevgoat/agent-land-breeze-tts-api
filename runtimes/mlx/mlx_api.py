"""Breeze TTS 2 API for the MLX runtime on Apple Silicon.

Serves the same API contract as runtimes/gguf/compat_api.py and
runtimes/pytorch/voice_api.py, including warm responses.  Runs natively
on macOS (no Docker) with Metal GPU acceleration via MLX.

Routes:
  POST /v1/audio/speech       -> streaming s16le 24 kHz PCM
  POST /v1/audio/speech/warm  -> 202 {"id","url"}; generates in background
  GET  /response/{id}         -> WAV or PCM (streams while generating)
  GET  /response/{id}/status  -> progress
  GET  /v1/voices             -> {"voices": [names]}
  GET  /v1/voices/{name}      -> metadata
  GET  /health                -> {"status":"ok","sample_rate":24000}
  GET  /usage                 -> API.md as Markdown

Voices use the same file layout as the other runtimes:
  <id>.wav + <id>.txt (+ optional <id>.json)
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
import wave
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from mlx_engine import MLXEngine

# ---------------------------------------------------------------- config

VOICES_DIR = Path(os.environ.get("BREEZE_VOICES_DIR", "./voices"))
VOICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SAMPLE_RATE = 24000
DEFAULT_CFG_SCALE = 1.0
SYNC_WAIT_SECONDS = float(os.environ.get("BREEZE_SYNC_WAIT_SECONDS", "30"))
MIN_SAVE_SECONDS = 1.0
MAX_SAVE_SECONDS = 30.0
MAX_NEW_TOKENS = int(os.environ.get("BREEZE_MAX_NEW_TOKENS", "1500"))

_models_dir = os.environ.get("BREEZE_MODELS_DIR", "./models")
_mlx_model_dir = os.environ.get("BREEZE_MLX_MODEL_DIR", "mlx-gust")
MODEL_PATH = os.environ.get("BREEZE_MLX_MODEL", f"{_models_dir}/{_mlx_model_dir}")

app = FastAPI(title="Breeze TTS (MLX)")
engine = MLXEngine(MODEL_PATH)
busy = asyncio.Lock()


# ---------------------------------------------------------------- audio conversion

def _find_audio_converter() -> str | None:
    """Find afconvert (macOS native) or ffmpeg for audio normalization."""
    if shutil.which("afconvert"):
        return "afconvert"
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    return None


AUDIO_CONVERTER = _find_audio_converter()


def _to_wav(data: bytes) -> bytes:
    """Any uploaded audio -> clean 24 kHz mono 16-bit WAV.

    Tries macOS-native afconvert first, falls back to ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".upload", delete=False) as tmp_in:
        tmp_in.write(data)
        tmp_in_path = tmp_in.name
    tmp_out_path = tmp_in_path + ".wav"
    try:
        if AUDIO_CONVERTER == "afconvert":
            try:
                subprocess.run(
                    ["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1",
                     tmp_in_path, tmp_out_path],
                    capture_output=True, timeout=60, check=True,
                )
                pcm = _extract_pcm(tmp_out_path)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pcm = _ffmpeg_to_pcm(data)
        elif AUDIO_CONVERTER == "ffmpeg":
            pcm = _ffmpeg_to_pcm(data)
        else:
            raise HTTPException(
                status_code=500,
                detail="No audio converter found. Install sox or ffmpeg: brew install ffmpeg",
            )
    finally:
        for p in (tmp_in_path, tmp_out_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    if len(pcm) < SAMPLE_RATE // 2:
        raise HTTPException(status_code=400, detail="ref_audio is empty or too short.")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return buf.getvalue()


def _ffmpeg_to_pcm(data: bytes) -> bytes:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-f", "s16le", "-c:a", "pcm_s16le", "pipe:1"],
            input=data, capture_output=True, timeout=60, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=400, detail="ref_audio is not a readable audio file.") from error
    pcm = proc.stdout[: len(proc.stdout) // 2 * 2]
    return pcm


def _extract_pcm(wav_path: str) -> bytes:
    with wave.open(wav_path, "rb") as f:
        return f.readframes(f.getnframes())


# ---------------------------------------------------------------- voice files

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
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    wav = VOICES_DIR / f"{voice_id}.wav"
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


# ---------------------------------------------------------------- streaming helpers

class _StreamCollector:
    """Async iterator that streams PCM chunks and optionally collects them."""

    def __init__(
        self,
        chunks: AsyncIterator[bytes],
        on_complete: Callable[[bytes], None] | None,
    ):
        self.chunks = chunks
        self.on_complete = on_complete
        self.released = False

    def _release(self) -> None:
        if not self.released:
            self.released = True
            busy.release()

    def __del__(self) -> None:
        self._release()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        pcm = bytearray() if self.on_complete else None
        completed = False
        try:
            async for chunk in self.chunks:
                if pcm is not None:
                    pcm += chunk
                yield chunk
            completed = True
        except Exception as error:
            print(f"MLX generation error: {error!r}", flush=True)
        finally:
            self._release()
        if completed and self.on_complete:
            try:
                self.on_complete(bytes(pcm))
            except Exception as error:
                print(f"saving voice failed: {error!r}", flush=True)


def _save_ref_audio(upload_data: bytes) -> str:
    """Save uploaded ref audio to a temp WAV file; return its path."""
    wav_data = _to_wav(upload_data)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(wav_data)
    tmp.close()
    return tmp.name


async def _speech(
    text: str,
    instruction: str | None,
    cfg_scale: float,
    seed: int,
    *,
    ref_audio_path: str | None = None,
    ref_text: str = "",
    on_complete: Callable[[bytes], None] | None = None,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Run one generation; the caller already holds ``busy``."""
    chunks = engine.generate(
        text,
        instruction=instruction,
        cfg_scale=cfg_scale,
        seed=seed,
        ref_audio_path=ref_audio_path,
        ref_text=ref_text,
    )
    out_headers = {
        "X-Sample-Rate": str(SAMPLE_RATE),
        "X-Sample-Format": "s16le",
        "Cache-Control": "no-store",
    }
    out_headers.update(headers or {})
    return StreamingResponse(
        _StreamCollector(chunks, on_complete),
        media_type="audio/pcm",
        headers=out_headers,
    )


# ---------------------------------------------------------------- routes

USAGE_PATH = Path(__file__).resolve().parent.parent.parent / "API.md"


@app.get("/usage", response_class=PlainTextResponse)
def usage() -> PlainTextResponse:
    if not USAGE_PATH.is_file():
        raise HTTPException(status_code=404, detail="API guide not found.")
    return PlainTextResponse(USAGE_PATH.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@app.get("/health")
def health() -> JSONResponse:
    if engine.ready:
        return JSONResponse({"status": "ok", "sample_rate": SAMPLE_RATE, "runtime": "mlx"})
    return JSONResponse({"status": "loading"}, status_code=503)


@app.get("/v1/voices")
def voices() -> dict[str, list[str]]:
    return {"voices": _list_voices()}


@app.get("/v1/voices/{name}")
def voice_info(name: str) -> dict:
    _, ref_text, meta = _resolve_voice(name)
    return {"id": name, "ref_text": ref_text, **meta}


# ---------------------------------------------------------------- speech validation

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
        _resolve_voice(voice)
    upload = (await ref_audio.read(), ref_text.strip()) if has_upload else None
    return SpeechArgs(text, voice, instruction, cfg_scale, seed, upload)


# ---------------------------------------------------------------- POST /v1/audio/speech

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
    try:
        await asyncio.wait_for(busy.acquire(), timeout=SYNC_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=409,
            detail=f"Busy: another request is still running after {SYNC_WAIT_SECONDS:g} s; retry, or use /v1/audio/speech/warm.",
        ) from None
    return await _start(args)


async def _start(args: SpeechArgs) -> StreamingResponse:
    """Start a generation; the caller has acquired ``busy``."""
    text, voice, instruction, cfg_scale, seed, upload = (
        args.text, args.voice, args.instruction, args.cfg_scale, args.seed, args.upload,
    )
    ref_audio_path = None
    try:
        if upload is not None:
            ref_audio_path = _save_ref_audio(upload[0])
            return await _speech(
                text, instruction, cfg_scale, seed,
                ref_audio_path=ref_audio_path, ref_text=upload[1],
            )

        status = "saved"
        if not voice and instruction:
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

        wav, preset_text, meta = _resolve_voice(voice)
        instruction = instruction or meta.get("instruction")
        return await _speech(
            text, instruction, cfg_scale, seed,
            ref_audio_path=str(wav), ref_text=preset_text,
            headers={"X-Voice-Id": voice, "X-Voice-Status": status},
        )
    except BaseException:
        busy.release()
        raise


# ---------------------------------------------------------------- warm responses

RESPONSES_DIR = Path(os.environ.get("BREEZE_RESPONSES_DIR", "./responses"))
RESPONSE_TTL = int(os.environ.get("BREEZE_RESPONSE_TTL_SECONDS", str(24 * 3600)))
MAX_PENDING = int(os.environ.get("BREEZE_MAX_PENDING", "32"))
MIN_LEAD_SECONDS = float(os.environ.get("BREEZE_MIN_LEAD_SECONDS", "0.75"))
SECONDS_PER_WORD = 0.32
LENGTH_MARGIN = 1.05
PLANNING_RATE_CAP = 0.95
SAFETY_SECONDS = 1.0
SAFETY_FRACTION = 0.08
CHUNK_MAX_SECONDS = 1.0  # MLX streams in small chunks by default
BYTES_PER_SECOND = SAMPLE_RATE * 2
WAV_HEADER = 44


def _wav_header(pcm_bytes: int | None) -> bytes:
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
    state: str = "queued"
    started: float | None = None
    finished: float | None = None
    pcm_bytes: int = 0
    error: str | None = None
    voice_headers: dict[str, str] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def notify(self) -> None:
        event, self.changed = self.changed, asyncio.Event()
        event.set()

    @property
    def generated_seconds(self) -> float:
        return self.pcm_bytes / BYTES_PER_SECOND

    def rate(self) -> float | None:
        if not self.started or not self.pcm_bytes:
            return None
        elapsed = (self.finished or time.time()) - self.started
        return self.generated_seconds / elapsed if elapsed > 0 else None

    def lead_needed(self, min_lead: float) -> float:
        rate = self.rate()
        if rate is None:
            return float("inf")
        total = max(self.estimated_seconds, self.generated_seconds * 1.1)
        rate = min(rate, 1.0) * PLANNING_RATE_CAP
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


def _voice_seconds_per_word(voice: str | None) -> float | None:
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


jobs: dict[str, WarmJob] = {}


async def _run_warm(job: WarmJob, args: SpeechArgs) -> None:
    try:
        await busy.acquire()
        job.state, job.started = "generating", time.time()
        job.notify()
        try:
            response = await _start(args)
        except BaseException:
            busy.release()
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
    except Exception as error:
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
async def _startup() -> None:
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - RESPONSE_TTL
    for path in RESPONSES_DIR.glob("*.wav"):
        with path.open("rb") as f:
            header = f.read(WAV_HEADER)
        data_size = struct.unpack("<I", header[40:44])[0] if len(header) == WAV_HEADER else 0xFFFFFFFF
        mtime = path.stat().st_mtime
        if data_size == 0xFFFFFFFF or mtime < cutoff:
            path.unlink(missing_ok=True)
            continue
        job = WarmJob(
            id=path.stem, path=path, estimated_seconds=data_size / BYTES_PER_SECOND,
            created=mtime, state="done", started=mtime, finished=mtime, pcm_bytes=data_size,
        )
        jobs[job.id] = job
    asyncio.get_running_loop().create_task(_cleanup_responses())

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, engine.load)
    asyncio.get_running_loop().create_task(engine.warmup())


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
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired response id.")
    return job.status()


async def _follow(job: WarmJob, wav: bool) -> AsyncIterator[bytes]:
    if wav:
        yield _wav_header(None)
    position = WAV_HEADER
    with job.path.open("rb") as f:
        while True:
            changed = job.changed
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
    if format not in ("wav", "pcm"):
        raise HTTPException(status_code=400, detail="format must be wav or pcm.")
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired response id.")
    while job.state in ("queued", "generating"):
        changed = job.changed
        if job.state == "generating" and job.generated_seconds >= job.lead_needed(min_lead):
            break
        try:
            await asyncio.wait_for(changed.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
    if job.state == "error":
        raise HTTPException(status_code=500, detail=f"Generation failed: {job.error}")
    headers = {"Cache-Control": "no-store", **job.voice_headers}
    if job.state == "done":
        if format == "wav":
            return FileResponse(job.path, media_type="audio/wav", headers=headers)
        return StreamingResponse(
            _follow(job, wav=False), media_type="audio/pcm",
            headers={**headers, "X-Sample-Rate": str(SAMPLE_RATE), "X-Sample-Format": "s16le"},
        )
    if format == "wav":
        return StreamingResponse(_follow(job, wav=True), media_type="audio/wav", headers=headers)
    return StreamingResponse(
        _follow(job, wav=False), media_type="audio/pcm",
        headers={**headers, "X-Sample-Rate": str(SAMPLE_RATE), "X-Sample-Format": "s16le"},
    )


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "7860")))
