#!/usr/bin/env python3
"""Detect the GPU and write matching settings into .env.

    python scripts/configure.py                      # detect, print, write .env
    python scripts/configure.py --runtime pytorch    # choose the runtime explicitly
    python scripts/configure.py --gpu 1 --dry-run    # pick GPU index 1, don't write

Sets CUDA_ARCHS, FLASH_ATTN_CUDA_ARCHS, BREEZE_GPU and COMPOSE_PROFILES, and picks a
GGUF file that fits the free VRAM. Everything else in .env is left as it is.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import envfile

# GGUF files from HoppouAI/Breeze-TTS-2.cpp with approximate VRAM needs (file + ~1.5 GB).
GGUF_BY_VRAM = [  # (min free GB, file)
    (5.5, "breeze-tts-2-q8_0.gguf"),
    (4.5, "breeze-tts-2-q6_k.gguf"),
    (3.5, "breeze-tts-2-q4_k.gguf"),
]
PYTORCH_MIN_FREE_GB = 19.0


def gpus() -> list[dict]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,compute_cap,memory.total,memory.used,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"nvidia-smi failed ({error}). An NVIDIA GPU and driver are required.")
    rows = []
    for line in out.strip().splitlines():
        index, name, cap, total, used, driver = (x.strip() for x in line.split(","))
        rows.append({"index": index, "name": name, "compute_cap": cap, "total_gb": int(total) / 1024,
                     "free_gb": (int(total) - int(used)) / 1024, "driver": driver})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime", choices=["gguf", "pytorch"], help="default: gguf")
    ap.add_argument("--gpu", help="GPU index to use (default: all GPUs visible, sized on GPU 0)")
    ap.add_argument("--dry-run", action="store_true", help="print the settings without writing .env")
    args = ap.parse_args()

    found = gpus()
    for g in found:
        print(f"GPU {g['index']}: {g['name']}, compute {g['compute_cap']}, "
              f"{g['total_gb']:.1f} GB total, {g['free_gb']:.1f} GB free, driver {g['driver']}")
    gpu = next((g for g in found if g["index"] == args.gpu), found[0])
    major, minor = (int(x) for x in gpu["compute_cap"].split("."))
    runtime = args.runtime or "gguf"

    changes = {
        "COMPOSE_PROFILES": runtime,
        "BREEZE_GPU": args.gpu if args.gpu is not None else "all",
        "CUDA_ARCHS": f"{major}{minor}",
        # FlashAttention 2.8.3 only builds 80/90/100/110/120; sm80 kernels run on all 8.x GPUs.
        "FLASH_ATTN_CUDA_ARCHS": "80" if major == 8 else str(major * 10),
    }
    if major < 8:
        print("warning: compute capability < 8.0; the PyTorch runtime needs Ampere or newer.")
    if runtime == "gguf":
        file = next((f for need, f in GGUF_BY_VRAM if gpu["free_gb"] >= need), None)
        if file is None:
            print(f"warning: only {gpu['free_gb']:.1f} GB free; even the smallest tested option may not fit.")
            file = GGUF_BY_VRAM[-1][1]
        changes["BREEZE_GGUF_FILE"] = file
    elif gpu["free_gb"] < PYTORCH_MIN_FREE_GB:
        print(f"warning: PyTorch fast mode needs ~18 GB; {gpu['free_gb']:.1f} GB free. "
              "Use --runtime gguf, or set BREEZE_PYTORCH_ARGS=--no-fast-all (~9 GB, slower than real time).")

    print("\nsettings:")
    for k, v in changes.items():
        print(f"  {k}={v}")
    if args.dry_run:
        print("\n(dry run: .env not changed)")
        return
    envfile.update(changes)
    print(f"\nwrote {envfile.ENV}. Next: python scripts/download_models.py && docker compose up -d --build")


if __name__ == "__main__":
    main()
