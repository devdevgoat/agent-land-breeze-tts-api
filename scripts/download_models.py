#!/usr/bin/env python3
"""Download the model weights selected in .env (needs: pip install "huggingface_hub[hf_xet]").

    python scripts/download_models.py                 # whatever COMPOSE_PROFILES selects
    python scripts/download_models.py --runtime pytorch

gguf    -> $BREEZE_MODELS_DIR/gguf/$BREEZE_GGUF_FILE      (from $BREEZE_GGUF_REPO, ~2.5-6.3 GB)
pytorch -> $BREEZE_MODELS_DIR/breeze-tts-2/               (from $BREEZE_PYTORCH_REPO, ~7.2 GB)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import envfile


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime", choices=["gguf", "pytorch"], action="append",
                    help="default: the runtimes in COMPOSE_PROFILES")
    args = ap.parse_args()

    from huggingface_hub import hf_hub_download, snapshot_download

    cfg = envfile.settings()
    models = Path(cfg.get("BREEZE_MODELS_DIR", "./models"))
    if not models.is_absolute():
        models = envfile.REPO / models
    runtimes = args.runtime or [p.strip() for p in cfg.get("COMPOSE_PROFILES", "gguf").split(",") if p.strip()]

    for runtime in runtimes:
        if runtime == "gguf":
            path = hf_hub_download(cfg["BREEZE_GGUF_REPO"], cfg["BREEZE_GGUF_FILE"], local_dir=models / "gguf")
        elif runtime == "pytorch":
            path = snapshot_download(cfg["BREEZE_PYTORCH_REPO"], local_dir=models / "breeze-tts-2",
                                     ignore_patterns=["assets/*"])
        else:
            raise SystemExit(f"unknown runtime {runtime!r}")
        print(f"{runtime}: {path}")


if __name__ == "__main__":
    main()
