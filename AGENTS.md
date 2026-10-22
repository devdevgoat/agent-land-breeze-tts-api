# AGENTS.md: deploying agent-land-breeze-tts-api

This is a step-by-step guide for an AI agent (or a person) to deploy this text-to-speech API on a fresh machine, adapt it to the hardware, and confirm it works. Follow the steps in order. Each step says how to check it before moving on.

## The one rule for changes: edit `.env`, not code

Everything that varies between machines lives in **`.env`** (created from the commented `.env.example`): runtime, GPU, model file, port, folders and tuning. `compose.yaml` reads only those variables. Edit code only when changing behaviour.

| To change... | Set in `.env` | Then |
|---|---|---|
| Runtime (GGUF, PyTorch or MLX) | `COMPOSE_PROFILES=gguf`, `pytorch` or `mlx` | NVIDIA: `docker compose up -d --build`; macOS: `breeze launch` |
| GPU architecture (NVIDIA) | `CUDA_ARCHS` (GGUF), `FLASH_ATTN_CUDA_ARCHS` (PyTorch); `scripts/configure.py` sets both | rebuild: `docker compose up -d --build` |
| Which GPU (NVIDIA) | `BREEZE_GPU=all` or an index / UUID from `nvidia-smi -L` | `docker compose up -d` |
| MLX model variant | `BREEZE_MLX_REPO` and `BREEZE_MLX_MODEL_DIR`; `breeze configure` sets both | `breeze download && breeze launch` |
| Model / quantization | `BREEZE_GGUF_FILE` (options listed in `.env.example`) | `python scripts/download_models.py && docker compose up -d` |
| Where the weights live | `BREEZE_MODELS_DIR` | restart the runtime |
| Port / LAN exposure | `BREEZE_PORT`, `BREEZE_BIND` (`127.0.0.1` = this machine only) | restart the runtime |
| Speed versus warm-response head start | `BREEZE_ENGINE_ARGS` (`--chunk-max 12` or `40`) | `docker compose up -d` |
| A second copy side by side | `COMPOSE_PROJECT_NAME`, `BREEZE_CONTAINER_NAME`, `BREEZE_PORT` | `docker compose up -d --build` |

## What you are deploying

| Runtime | Code | GPU memory | Speed | Has warm responses and `/usage` |
|---|---|---|---|---|
| **GGUF (default)** | `runtimes/gguf/` (Breeze-TTS-2.cpp + `compat_api.py`) | ~4–5 GB VRAM | TTFA ~0.35–0.9 s, RTF ~0.8–1.0 (RTX 4090) | yes |
| PyTorch | `runtimes/pytorch/` (`voice_api.py` + upstream submodule) | ~17–18 GB VRAM | TTFA ~0.1–0.5 s, RTF ~0.6–0.7 (RTX 4090) | no |
| **MLX (macOS)** | `runtimes/mlx/` (`mlx_api.py` + `mlx_engine.py`) | ~4 GB unified | TTFA ~0.5 s, RTF ~1.9 (M4) | yes |

[BENCHMARKS.md](BENCHMARKS.md) has the full measurements and the test hardware. **On NVIDIA, deploy GGUF unless the user asks otherwise. On macOS Apple Silicon, deploy MLX.** All runtimes serve the same speech API on one port (default 7860), so run only one at a time. The full API is in [API.md](API.md).

## Rules

- **Model licence:** the Breeze TTS 2 weights and the speech they produce are **research and non-commercial use only** (BreezeBlue licence). Tell the user before deploying for anything commercial. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Nothing private in git:** never commit `.env`, `models/`, `responses/` or voice clips. `.gitignore` already excludes them.
- **Consent for cloning:** voice cloning needs the consent of the person whose voice it is.
- **No internet exposure:** the API has **no authentication**. Keep it on a LAN or VPN, and never forward the port to the internet.
- **Ask before risky changes:** get the user's go-ahead before changing a firewall, stopping containers you didn't start, or **starting a second GPU workload next to a running one**. A PyTorch instance starting next to a live GGUF engine has crashed the GGUF engine (see BENCHMARKS.md).

## Deploying on macOS Apple Silicon (M1–M5)

If the target machine is a Mac with Apple Silicon, follow these steps instead of the NVIDIA steps below.

### M1. Download and run setup

Download the [`breeze` CLI](https://github.com/ParkWardRR/agent-land-breeze-tts-api/releases/latest) (Developer ID signed + Apple notarized) and run the one-command setup:

```bash
curl -fsSL https://github.com/ParkWardRR/agent-land-breeze-tts-api/releases/latest/download/breeze-macos-arm64.tar.gz | tar xz
chmod +x breeze
./breeze setup    # checks prerequisites, clones repo, configures hardware, downloads model
```

- **Not arm64?** Intel Macs are not supported. Use the NVIDIA path with a GPU machine instead.
- **No Homebrew?** `breeze setup` needs it for Python 3.12 and sox. Install from https://brew.sh first.
- **Disk:** about 4–8 GB for MLX weights depending on the model variant.

`breeze setup` installs Python 3.12 and sox via Homebrew if missing, clones the repo, detects the chip and memory, writes `.env`, and downloads the INT8 model weights. Check `.env` and adjust the port if needed.

### M2. Start

```bash
cd agent-land-breeze-tts-api
breeze launch       # creates .venv, installs deps, starts the server
# In another terminal:
until curl -fsS http://127.0.0.1:7860/health; do sleep 3; done   # {"status":"ok","sample_rate":24000,"runtime":"mlx"}
```

First run creates a Python 3.12 venv, installs all dependencies (including `breeze-tts-mlx` from source), then starts the server. Subsequent runs skip setup and start immediately. First inference triggers Metal shader compilation (15–30 s).

### M3. Verify, then benchmark

```bash
curl -s http://127.0.0.1:7860/v1/voices          # {"voices": []} on a fresh install
.venv/bin/python client.py "Hello! The speech server is up and running." \
  --instruction "A calm, friendly adult voice speaking clearly." --cfg-scale 4 --seed 7 -o hello.wav
curl -s http://127.0.0.1:7860/usage | head -3      # the API guide
breeze benchmark --label "<chip>, mlx" --notes "first run after warm-up"
```

### M4. Auto-start on login (optional, ask the user first)

```bash
breeze service install     # installs a launchd agent; restarts on crash
# To remove: breeze service remove
```

### M5. Make it reachable on the LAN

macOS allows incoming connections by default on the local network. Check from another machine with `curl http://<mac-ip>:<port>/health`. If the macOS firewall blocks it, the user will see a system dialog to allow the connection.

---

## Deploying on NVIDIA GPU (Linux / Windows)

## 1. Check prerequisites

```bash
nvidia-smi                                          # NVIDIA GPU; note free memory
docker version && docker compose version            # Docker Engine (Linux) or Docker Desktop (Windows, WSL2)
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi   # GPU visible inside containers
git --version
python3 --version                                   # 3.10+ for the scripts
python3 -m pip install -U "huggingface_hub[hf_xet]" requests
```

- **GPU not visible in containers:** install the NVIDIA Container Toolkit (Linux), or enable WSL2 GPU support in Docker Desktop. Stop and tell the user if you can't.
- **Disk:** about 4 GB for the GGUF weights and about 10 GB for the build. PyTorch needs about 7 GB of weights and about 30 GB for its image.
- **Windows Git Bash:** use `python` if `python3` isn't found.

## 2. Get the code and configure for this machine

```bash
git clone --recurse-submodules https://github.com/devdevgoat/agent-land-breeze-tts-api.git
cd agent-land-breeze-tts-api
python3 scripts/configure.py          # detects the GPU; writes CUDA_ARCHS etc. into .env (add --runtime pytorch for PyTorch)
```

`configure.py` prints the GPU and the settings it wrote. Check `.env` and adjust anything else, such as the port. It warns if the GPU is too small for the chosen runtime.

## 3. Download the model

```bash
python3 scripts/download_models.py    # fetches what .env selects into $BREEZE_MODELS_DIR
```

Check: for GGUF, `models/gguf/<BREEZE_GGUF_FILE>` exists (3.57 GB for q8_0).

## 4. Build and start

```bash
docker compose up -d --build
until curl -fsS http://127.0.0.1:7860/health; do sleep 3; done   # {"status":"ok","sample_rate":24000}
docker compose logs --tail 20                                   # GGUF: should show "backend: CUDA0"
```

| Runtime | Build | Start |
|---|---|---|
| GGUF | ~5 min | ~15–20 s |
| PyTorch | ~25 min | ~1.5–2.5 min |

Use your `BREEZE_PORT` if you changed it. **`backend: CPU` in the GGUF logs** means the GPU isn't reaching the container, or `CUDA_ARCHS` is wrong. Fix `.env` and rebuild.

## 5. Verify, then benchmark

```bash
curl -s http://127.0.0.1:7860/v1/voices          # {"voices": []} on a fresh install
python3 client.py "Hello! The speech server is up and running." \
  --instruction "A calm, friendly adult voice speaking clearly." --cfg-scale 4 --seed 7 -o hello.wav
curl -s http://127.0.0.1:7860/usage | head -3      # GGUF only: the API guide
python3 scripts/benchmark.py --label "<gpu>, <runtime>" --notes "<anything else using the GPU>"
```

- **`client.py`:** saves `hello.wav` (about 3–4 s) and prints time to first audio, wall time and RTF. Its first run creates a designed voice in `voices/`.
- **`benchmark.py`:** saves `benchmarks/results/<date>_<gpu>_<runtime>.json` and prints Markdown rows. Compare them with BENCHMARKS.md.
- **What good looks like:** an RTF above 1.0 means generation is slower than playback. On GGUF, warm responses still work, because the server waits longer before starting playback.
- **Report:** the benchmark summary goes to the user. If they want it published, add a row to BENCHMARKS.md.

## 6. Voices (optional)

- **Designed voices:** saved in `voices/` automatically.
- **Hand-made voice:** add `voices/<name>.wav` (5–15 s of clean speech) plus `voices/<name>.txt` (the exact transcript), then use `voice=<name>`. Only with recordings the user has the right to use. See [voices/README.md](voices/README.md).

## 7. Make it reachable on the LAN (ask the user first)

- **Linux:** usually reachable as is. Check from another machine with `curl http://<server-ip>:<port>/health`.
- **Windows:** inbound traffic is blocked by default. **Ask the user** before adding a rule. The command, for an Administrator PowerShell, allows the local subnet only:

```powershell
New-NetFirewallRule -DisplayName "Breeze TTS 7860" -Direction Inbound -Protocol TCP -LocalPort 7860 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

## Operate

```bash
docker compose stop            # stop (stays stopped across reboots; restart: unless-stopped)
docker compose start           # start again
docker compose logs -f         # requests, voice saves, warm-job timings ("warm <id>: done, ..., rtf ...")
docker compose up -d           # apply .env changes
docker compose up -d --build   # after changing CUDA_ARCHS, BREEZE_CPP_COMMIT or runtime code
```

**Switching runtimes:** stop the current one, change `COMPOSE_PROFILES` in `.env`, run `download_models.py`, then `docker compose up -d --build`. Use `docker compose --profile <old> stop` to stop a runtime that isn't in `COMPOSE_PROFILES` any more.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `409` on `/v1/audio/speech` | One request at a time. Retry with backoff, or use `/v1/audio/speech/warm`, which queues |
| `503` for ~15 s after a start | Loading. Warm requests wait for the engine automatically (up to `BREEZE_ENGINE_WAIT_SECONDS`) |
| `port is already allocated` | The other runtime (or something else) is using the port. Stop it, or change `BREEZE_PORT` |
| RTF far above 1, or stalls in warm playback | Something else is using the GPU, and Windows spills GPU memory into system RAM. Check `nvidia-smi`, and free memory or stop the other workload |
| GGUF engine log `CUDA error: unspecified launch failure` | The GPU faulted, usually from memory pressure caused by another workload starting. The container restarts by itself; find what else is on the GPU |
| `backend: CPU` | GPU not passed through, or wrong `CUDA_ARCHS` |
| PyTorch build fails in FlashAttention with `IndexError` | `FLASH_ATTN_CUDA_ARCHS` must be 80/90/100/110/120 (use 80 for any 8.x GPU) |
| MLX: first request is very slow | Metal shader compilation on first inference. Subsequent requests are fast. The server does a warm-up at startup |
| MLX: `ModuleNotFoundError: No module named 'breeze_tts_mlx'` | Install from source: `git clone https://github.com/rishikksh20/breeze-tts-mlx.git && pip install -e breeze-tts-mlx/` |
| MLX: out of memory on 8 GB Mac | Use INT8 model: `breeze configure --model int8` then re-download |
| MLX: `ImportError: ... mlx ...` | Requires macOS ARM64 (Apple Silicon) with Python 3.12. Intel Macs are not supported |

## When you're done

Report to the user:
- the URL (`http://<server-ip>:<port>`) and `/usage`,
- the runtime and model,
- the GPU and the benchmark summary,
- what you verified,
- anything you skipped (for example the firewall) and why.
