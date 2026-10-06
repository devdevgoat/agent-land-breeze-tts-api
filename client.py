"""Stream speech from the Breeze TTS API and save it as a WAV file.

Examples:
  python client.py "(sigh) Hello there." --instruction "A calm, warm male voice." -o out.wav
  python client.py "Hello there!" --voice narrator -o narrator.wav    # server-side preset
  python client.py "Hi again." --ref-audio ref.wav --ref-text "Exact transcript of ref." -o clone.wav
  python client.py "Hello." --play            # play while streaming (needs `pip install sounddevice`)
"""

from __future__ import annotations

import argparse
import time
import wave

import requests


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("text")
    p.add_argument("--voice", help="server-side preset name (see GET /v1/voices)")
    p.add_argument("--instruction")
    p.add_argument("--ref-audio")
    p.add_argument("--ref-text", default="")
    p.add_argument("--cfg-scale", type=float, default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--url", default="http://127.0.0.1:7860")
    p.add_argument("-o", "--output", default="out.wav")
    p.add_argument("--play", action="store_true")
    a = p.parse_args()

    data = {"text": a.text, "seed": str(a.seed), "ref_text": a.ref_text}
    if a.voice:
        data["voice"] = a.voice
    if a.instruction:
        data["instruction"] = a.instruction
    cfg = a.cfg_scale if a.cfg_scale is not None else (4.0 if a.instruction else 1.0)
    data["cfg_scale"] = str(cfg)
    files = {"ref_audio": open(a.ref_audio, "rb")} if a.ref_audio else None

    stream = None
    t0 = time.perf_counter()
    first = None
    total = 0
    with requests.post(f"{a.url}/v1/audio/speech", data=data, files=files, stream=True) as r:
        r.raise_for_status()
        rate = int(r.headers.get("X-Sample-Rate", 24000))
        if a.play:
            import sounddevice as sd

            stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
            stream.start()
        with wave.open(a.output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            leftover = b""
            for chunk in r.iter_content(chunk_size=None):
                if first is None:
                    first = time.perf_counter() - t0
                chunk = leftover + chunk
                cut = len(chunk) - (len(chunk) % 2)
                chunk, leftover = chunk[:cut], chunk[cut:]
                wav.writeframes(chunk)
                total += len(chunk)
                if stream:
                    stream.write(chunk)
    if stream:
        stream.stop()
    elapsed = time.perf_counter() - t0
    audio_s = total / 2 / rate
    if "X-Voice-Id" in r.headers:
        print(f"voice: {r.headers['X-Voice-Id']} ({r.headers.get('X-Voice-Status')})")
    print(
        f"saved {a.output}: {audio_s:.2f}s audio | TTFA {first * 1000:.0f} ms | "
        f"wall {elapsed:.2f}s | RTF {elapsed / max(audio_s, 1e-9):.2f}"
    )


if __name__ == "__main__":
    main()
