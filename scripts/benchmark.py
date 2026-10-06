#!/usr/bin/env python3
"""Benchmark a running server and save the results with the hardware and config.

    python scripts/benchmark.py                          # server from .env (127.0.0.1:$BREEZE_PORT)
    python scripts/benchmark.py --url http://host:7860 --runs 5 --label "my-gpu"

Measures, for a designed voice (created once, then reused):
  * short line (~15 words) and a 50-word reply: time to first audio, wall time, real-time factor
  * warm responses (GGUF runtime): time to get an id, wait before first audio, and the smallest
    buffer margin a real-time player would have (negative = it would stall)
  * GPU memory: idle before the run and peak during it (whole card, all processes)

Writes benchmarks/results/<date>_<gpu>_<runtime>.json and prints Markdown rows for BENCHMARKS.md.
Needs: pip install requests. Run it on the server machine to capture GPU info.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import re
import statistics
import subprocess
import threading
import time
from pathlib import Path

import requests

import envfile

BYTES_PER_SECOND = 24000 * 2
VOICE = {"instruction": "A calm, friendly adult voice speaking clearly.", "cfg_scale": "4", "seed": "7"}
SHORT = "The deployment finished a minute ago and every check came back green, so we are good to go."
WORDS_50 = ("I just finished reviewing the deployment logs, and everything looks healthy. The new build passed all "
            "tests, response times dropped by about twenty percent, and there were no errors overnight. I would "
            "suggest we roll it out to the remaining servers this afternoon, then keep an eye on memory usage until "
            "tomorrow morning.")


def nvidia(query: str) -> list[str]:
    try:
        return subprocess.run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, check=True).stdout.strip().splitlines()
    except (OSError, subprocess.CalledProcessError):
        return []


def cpu_name() -> str:
    try:
        if platform.system() == "Windows":
            return subprocess.run(["powershell", "-NoProfile", "-Command",
                                   "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"],
                                  capture_output=True, text=True, timeout=20).stdout.strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor() or platform.machine()


def os_name() -> str:
    if platform.system() == "Windows":
        build = int(platform.version().split(".")[-1])
        return f"Windows {'11' if build >= 22000 else '10'} (build {build})"
    return platform.platform()


class VramSampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            used = nvidia("memory.used")
            if used:
                self.peak = max(self.peak, max(int(x) for x in used))
            time.sleep(0.25)


def speak(url: str, text: str) -> dict:
    t0 = time.perf_counter()
    first = None
    size = 0
    with requests.post(f"{url}/v1/audio/speech", data={"text": text, **VOICE}, stream=True, timeout=600) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=None):
            if first is None and chunk:
                first = time.perf_counter() - t0
            size += len(chunk)
    wall = time.perf_counter() - t0
    audio = size / BYTES_PER_SECOND
    return {"ttfa_s": round(first, 3), "wall_s": round(wall, 2), "audio_s": round(audio, 2),
            "rtf": round(wall / audio, 3), "voice": r.headers.get("X-Voice-Id")}


def warm(url: str, text: str) -> dict | None:
    t0 = time.perf_counter()
    r = requests.post(f"{url}/v1/audio/speech/warm", data={"text": text, **VOICE}, timeout=30)
    if r.status_code == 404:
        return None  # runtime without warm responses (PyTorch)
    r.raise_for_status()
    id_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    first = None
    have = 0.0
    margin = float("inf")
    buf = 0
    with requests.get(f"{url}{r.json()['url']}", stream=True, timeout=600) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=None):
            now = time.perf_counter()
            buf += len(chunk)
            audio = max(0, buf - 44) / BYTES_PER_SECOND
            if first is None and audio > 0:
                first = now
            elif first is not None and have > 0:
                margin = min(margin, have - (now - first))  # buffered vs played, just before this chunk
            have = audio
    return {"id_ms": round(id_ms), "wait_first_audio_s": round(first - t1, 2), "audio_s": round(have, 2),
            "min_margin_s": round(margin, 2), "stalled": margin < 0}


def summary(rows: list[dict], key: str) -> str:
    values = [r[key] for r in rows]
    return f"{min(values):g}–{max(values):g}" if len(values) > 1 else f"{values[0]:g}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cfg = envfile.settings()
    ap.add_argument("--url", default=f"http://127.0.0.1:{cfg.get('BREEZE_PORT', '7860')}")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--label", default="", help="free-text label stored with the results")
    ap.add_argument("--notes", default="", help="conditions worth recording (e.g. other GPU load)")
    args = ap.parse_args()

    health = requests.get(f"{args.url}/health", timeout=10)
    health.raise_for_status()
    gpu = nvidia("name,compute_cap,memory.total,driver_version")
    system = {
        "gpu": gpu[0] if gpu else "unknown (nvidia-smi not available here)",
        "cpu": cpu_name(),
        "os": os_name(),
    }
    config = {k: cfg.get(k) for k in ("COMPOSE_PROFILES", "BREEZE_GGUF_FILE", "BREEZE_ENGINE_ARGS",
                                      "BREEZE_PYTORCH_ARGS", "CUDA_ARCHS", "BREEZE_CPP_COMMIT")}
    vram_idle = max((int(x) for x in nvidia("memory.used")), default=None)

    sampler = VramSampler()
    sampler.start()
    # First request creates (or reuses) the designed voice: its reference clip is this sentence,
    # so use a full one - a one-word clip makes a poor reference and slows later generation.
    speak(args.url, SHORT)
    short = [speak(args.url, SHORT) for _ in range(args.runs)]
    long = [speak(args.url, WORDS_50) for _ in range(args.runs)]
    warm_runs = []
    for _ in range(max(1, args.runs - 1)):
        result = warm(args.url, WORDS_50)
        if result is None:
            break
        warm_runs.append(result)
    sampler.stop.set()

    results = {
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "label": args.label, "notes": args.notes, "url": args.url, "system": system, "config": config,
        "vram_mb": {"idle": vram_idle, "peak": sampler.peak or None},
        "short_line": short, "words_50": long, "warm": warm_runs,
    }
    gpu_name = re.sub(r"[^A-Za-z0-9]+", "-", system["gpu"].split(",")[0]).strip("-").lower()
    out = envfile.REPO / "benchmarks" / "results" / (
        f"{dt.date.today()}_{gpu_name}_{config['COMPOSE_PROFILES'] or 'unknown'}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    print(f"saved {out.relative_to(envfile.REPO)}\n")
    print("| Test | Time to first audio (s) | Wall (s) | Audio (s) | RTF |")
    print("|---|---|---|---|---|")
    for name, rows in (("short line", short), ("50 words", long)):
        print(f"| {name} | {summary(rows, 'ttfa_s')} | {summary(rows, 'wall_s')} | "
              f"{summary(rows, 'audio_s')} | {summary(rows, 'rtf')} |")
    if warm_runs:
        print("\n| Warm (50 words) | id (ms) | wait before first audio (s) | min buffer margin (s) | stalled |")
        print("|---|---|---|---|---|")
        for w in warm_runs:
            print(f"| | {w['id_ms']} | {w['wait_first_audio_s']} | {w['min_margin_s']:+} | {w['stalled']} |")
    print(f"\nGPU memory (whole card): idle {vram_idle} MB, peak {sampler.peak} MB")


if __name__ == "__main__":
    main()
