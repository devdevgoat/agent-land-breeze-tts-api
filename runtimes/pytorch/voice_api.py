"""Breeze TTS API with server-side voices.

Wraps the upstream ``breeze_infer.api`` app (left unmodified) and adds:
  * ``voice`` form field on POST /v1/audio/speech -> clone from a saved voice
  * Designed-voice reuse: the first request with an ``instruction`` (and no
    reference) is saved as a voice; its id comes back in the ``X-Voice-Id``
    header. Later requests with the same instruction + seed, or with
    ``voice=<id>``, clone from that saved clip so the voice stays the same
    for any text. (Instruction + seed alone does not: sampling diverges as
    soon as the text changes.)
  * GET /v1/voices -> {"voices": [names]};  GET /v1/voices/{id} -> metadata

A voice is a set of files in BREEZE_VOICES_DIR (default /voices):
  narrator.wav    clean reference speech (~5-15 s)
  narrator.txt    exact transcript of narrator.wav
  narrator.json   optional metadata; designed voices store instruction, seed, cfg_scale
Hand-made voices are picked up per request; no restart needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import wave
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import torch
from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.routing import Route

from breeze_infer import api

VOICES_DIR = Path(os.environ.get("BREEZE_VOICES_DIR", "/voices"))
VOICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SAMPLE_RATE = 24000
# A designed voice is only saved if its first clip makes a usable reference.
MIN_SAVE_SECONDS = 1.0
MAX_SAVE_SECONDS = 30.0


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
    # Write the wav last-but-one and rename into place, so a voice is only
    # listed once its audio and transcript are both complete.
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


# Drop the upstream route so ours owns the path (same contract plus `voice`).
api.app.router.routes = [
    r
    for r in api.app.router.routes
    if not (isinstance(r, Route) and r.path == "/v1/audio/speech")
]


@api.app.get("/v1/voices")
def voices() -> dict[str, list[str]]:
    return {"voices": _list_voices()}


@api.app.get("/v1/voices/{name}")
def voice_info(name: str) -> dict:
    _, ref_text, meta = _resolve_voice(name)
    return {"id": name, "ref_text": ref_text, **meta}


@api.app.post("/v1/audio/speech")
async def speech(
    text: str = Form(...),
    voice: str | None = Form(None),
    instruction: str | None = Form(None),
    cfg_scale: float = Form(api.DEFAULT_CFG_SCALE),
    ref_audio: UploadFile | None = File(None),
    ref_text: str = Form(""),
    seed: int = Form(42),
) -> StreamingResponse:
    has_upload = ref_audio is not None and bool(ref_audio.filename)
    instruction = instruction.strip() if instruction else None
    print(
        f"speech request: voice={voice or ('upload' if has_upload else '-')} "
        f"cfg={cfg_scale} seed={seed} instruction={instruction!r} "
        f"text={len(text)} chars: {text[:200]!r}",
        flush=True,
    )
    if voice and (has_upload or ref_text.strip()):
        raise HTTPException(
            status_code=400, detail="Use either voice or ref_audio/ref_text, not both."
        )
    if has_upload:
        return await _speech(text, instruction, cfg_scale, ref_audio, ref_text, seed)

    status = "saved"
    if not voice and instruction:
        # Voice design: reuse the voice saved for this instruction + seed, if any.
        voice = _designed_voice_id(instruction, seed)
        if not (VOICES_DIR / f"{voice}.wav").is_file():
            meta = {"instruction": instruction, "seed": seed, "cfg_scale": cfg_scale}
            response = await _speech(
                text, instruction, cfg_scale, None, "", seed,
                on_complete=lambda pcm: _save_designed_voice(voice, pcm, text, meta),
            )
            response.headers["X-Voice-Id"] = voice
            response.headers["X-Voice-Status"] = "created"
            return response
        status = "reused"
    if not voice:
        return await _speech(text, instruction, cfg_scale, None, "", seed)

    wav, preset_text, meta = _resolve_voice(voice)
    # A designed voice keeps its describing instruction unless the request overrides it.
    instruction = instruction or meta.get("instruction")
    with wav.open("rb") as handle:
        # Upstream copies the upload to a temp file before returning the response,
        # so the voice file can be closed as soon as this call returns.
        upload = UploadFile(file=handle, filename=wav.name)
        response = await _speech(text, instruction, cfg_scale, upload, preset_text, seed)
    response.headers["X-Voice-Id"] = voice
    response.headers["X-Voice-Status"] = status
    return response


def _die_on_cuda_fault(error: BaseException) -> None:
    # A CUDA fault (e.g. illegal memory access) poisons the process's CUDA context:
    # every later request fails while /health still says ok. Exit so Docker's
    # restart policy brings up a clean server (~2 min warmup on the fast path).
    print(f"FATAL CUDA error, exiting for a clean restart: {error}", flush=True)
    os._exit(1)


async def _guard_stream(
    chunks: AsyncIterator[bytes], on_complete: Callable[[bytes], None] | None
) -> AsyncIterator[bytes]:
    pcm = bytearray() if on_complete else None
    try:
        async for chunk in chunks:
            if pcm is not None:
                pcm += chunk
            yield chunk
    except torch.AcceleratorError as error:
        _die_on_cuda_fault(error)
    # Only reached when generation finished and the client read everything; a
    # disconnect stops at `yield`, so a partial clip is never saved.
    if on_complete:
        try:
            on_complete(bytes(pcm))
        except Exception as error:  # saving must never break a finished response
            print(f"saving voice failed: {error!r}", flush=True)


async def _speech(
    *args, on_complete: Callable[[bytes], None] | None = None
) -> StreamingResponse:
    try:
        response = await api.speech(*args)
    except torch.AcceleratorError as error:
        _die_on_cuda_fault(error)
    except ValueError as error:  # bad input combination, e.g. cfg_scale on a clone with no instruction
        raise HTTPException(status_code=400, detail=str(error)) from error
    response.body_iterator = _guard_stream(response.body_iterator, on_complete)
    return response


if __name__ == "__main__":
    api.main()
