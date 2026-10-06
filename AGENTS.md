# AGENTS.md: deploying agent-land-breeze-tts-api

This is a step-by-step guide for an AI agent (or a person) to deploy this text-to-speech API on a fresh machine and confirm it works. Follow the steps in order. Each step says how to check it before moving on.

## What you are deploying

- **What it is:** a self-hosted [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2) speech server on an NVIDIA GPU, in Docker.
- **Where it listens:** port **7860**. The full API is in [API.md](API.md); a running server also serves it at `GET /usage`.
- **Two runtimes** with the same API. Run only one at a time, because both use port 7860:

| Runtime | Path | GPU memory | Speed | Use when |
|---|---|---|---|---|
| **GGUF (default)** | `gguf/` | ~4–5 GB | first audio ~0.35–0.9 s, generation ~0.85–0.95× real time | Almost always. Leaves the GPU free for other work |
| PyTorch (original) | `docker-compose.yml` at the repo root, `server/`, `src/` | ~17–18 GB | first audio ~0.1–0.5 s, ~0.66× real time | You have a GPU to spare and want maximum speed |

**Deploy the GGUF runtime unless the user asks otherwise.**

## Rules

- **Model licence:** the Breeze TTS 2 weights and the speech they produce are licensed for **research and non-commercial use only** (BreezeBlue licence). Tell the user before deploying for anything commercial. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **No weights in git:** never commit model weights, `models/`, `responses/` or voice clips. `.gitignore` already excludes them.
- **Consent for cloning:** voice cloning needs the consent of the person whose voice it is. Don't create voices from other people's recordings unless the user confirms they have that right.
- **No internet exposure:** the API has **no authentication**. Keep it on a LAN or behind a VPN. Never forward port 7860 to the internet.
- **Ask first:** get the user's go-ahead before changing a firewall, or stopping a container you didn't start.

## 1. Check prerequisites

Run these and confirm each:

```bash
nvidia-smi                                          # an NVIDIA GPU with >= 8 GB free; note "CUDA Version"
nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader   # e.g. "NVIDIA GeForce RTX 4090, 8.9"
docker version                                      # Docker Engine (Linux) or Docker Desktop (Windows/macOS-WSL2)
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi   # GPU visible inside containers
git --version
python3 --version                                   # 3.10+ (only needed for the weight download and the test client)
```

- **GPU not visible in containers:** install the NVIDIA Container Toolkit (Linux), or enable WSL2 GPU support in Docker Desktop (Windows). Stop and tell the user if you can't.
- **Note the compute capability without the dot** (8.9 becomes `89`). You need it in step 3.
- **Disk:** about 4 GB for the GGUF weights, plus about 10 GB for the build.

## 2. Get the code and the weights

```bash
git clone https://github.com/devdevgoat/agent-land-breeze-tts-api.git
cd agent-land-breeze-tts-api
python3 -m pip install -U "huggingface_hub[hf_xet]"
python3 -c "from huggingface_hub import hf_hub_download as d; print(d('HoppouAI/Breeze-TTS-2.cpp', 'breeze-tts-2-q8_0.gguf', local_dir='models/gguf'))"
```

Check: `models/gguf/breeze-tts-2-q8_0.gguf` exists and is about 3.57 GB. (On Windows Git Bash, run Python with `python` instead of `python3` if needed.)

## 3. Build and start the GGUF runtime

```bash
# Use the compute capability from step 1 (89 = RTX 40xx/Ada, 86 = RTX 30xx/Ampere, 90 = H100/Hopper, 120 = RTX 50xx/Blackwell)
CUDA_ARCHS=89 docker compose -f gguf/docker-compose.yml up -d --build
```

- **Build:** about 5 minutes. It compiles `breeze-server` from Breeze-TTS-2.cpp at a pinned commit, with the ggml CUDA backend.
- **Start:** loading takes about 15–20 s after the build.

Wait until it's healthy:

```bash
until curl -fsS http://127.0.0.1:7860/health; do sleep 3; done   # -> {"status":"ok","sample_rate":24000}
docker compose -f gguf/docker-compose.yml logs --tail 20          # should show "backend: CUDA0"
```

- **`backend: CPU` instead of CUDA:** the GPU isn't reaching the container (go back to step 1), or `CUDA_ARCHS` is wrong for this GPU. Fix it and rebuild with `--build`.

## 4. Verify it works

```bash
curl -s http://127.0.0.1:7860/v1/voices          # {"voices": []} on a fresh install - that's expected

# Design a voice from a description (saved and reused automatically; the id comes back in X-Voice-Id)
curl -sS -D headers.txt -X POST http://127.0.0.1:7860/v1/audio/speech \
  -F "text=Hello! The speech server is up and running." \
  -F "instruction=A calm, friendly adult voice speaking clearly." \
  -F "cfg_scale=4" -F "seed=7" --output test.pcm
grep -i x-voice headers.txt                       # X-Voice-Id: v-..., X-Voice-Status: created

# Convert the raw PCM to WAV to listen (ffmpeg on the host, or inside the container)
ffmpeg -y -f s16le -ar 24000 -ac 1 -i test.pcm test.wav

# Warm flow: get an id at once, play it from a URL
curl -s -X POST http://127.0.0.1:7860/v1/audio/speech/warm -F "text=Warm response test." \
  -F "instruction=A calm, friendly adult voice speaking clearly." -F "cfg_scale=4" -F "seed=7"
# -> {"id": "...", "url": "/response/<id>"}; then:
curl -s http://127.0.0.1:7860/response/<id> --output warm.wav
```

`test.pcm` should be about 3–4 s of audio, so roughly 150–200 KB. `python3 client.py "Hello" --instruction "A calm voice." -o hi.wav` does the same in one step and prints the timings.

## 5. Add saved voices (optional)

- **Designed voices** (step 4) are saved in `voices/` automatically.
- **Hand-made voices:** for a voice cloned from a recording, put two files in `voices/`:
  - `<name>.wav`: 5–15 s of clean speech, no music or noise.
  - `<name>.txt`: the exact transcript of that recording.

  The server picks them up on the next request, with no restart needed. Then use `-F voice=<name>`. Only do this with recordings the user has the right to use.

## 6. Make it reachable on the LAN (ask the user first)

- **Linux:** Docker publishes `0.0.0.0:7860` and Docker's own iptables rules usually let LAN traffic in. Check from another machine with `curl http://<server-ip>:7860/health`.
- **Windows:** inbound traffic is blocked by default. **Ask the user** before adding a rule. The command, for an Administrator PowerShell, allows the local subnet only:

```powershell
New-NetFirewallRule -DisplayName "Breeze TTS 7860" -Direction Inbound -Protocol TCP -LocalPort 7860 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

## 7. Operate it

```bash
docker compose -f gguf/docker-compose.yml stop        # stop (it stays stopped across reboots)
docker compose -f gguf/docker-compose.yml start       # start again
docker compose -f gguf/docker-compose.yml logs -f     # per-request logs, voice saves, warm-job timings
```

It restarts automatically after a crash or reboot (`restart: unless-stopped`) unless it was stopped on purpose.

**Settings** (environment variables, or edit `gguf/docker-compose.yml`):

| Variable | Default | Meaning |
|---|---|---|
| `CUDA_ARCHS` | `89` | GPU architecture to compile for (build time) |
| `BREEZE_ENGINE_ARGS` | `--chunk-max 12` | Engine flags. `--chunk-max 40` is about 8% faster overall, but warm responses then wait about 4 s before playing |
| `BREEZE_RESPONSE_TTL_SECONDS` | `86400` | How long warm responses are kept |

## Optional: the PyTorch runtime instead

Only if the user wants maximum speed and has about 18 GB of GPU memory free.

```bash
git submodule update --init                       # upstream inference code into src/
python3 -c "from huggingface_hub import snapshot_download as s; s('BreezeBlue/Breeze-TTS-2', local_dir='models/breeze-tts-2')"   # ~7.2 GB
docker compose -f gguf/docker-compose.yml stop    # free port 7860
docker compose up -d --build                      # ~25 min build (compiles FlashAttention), ~2 min warmup
```

- **Switching back:** `docker compose stop && docker compose -f gguf/docker-compose.yml start`.
- **Already handled:**
  - FlashAttention 2.8.3 can't build for arch `89`, so the compose file builds `80`, which also runs on Ada.
  - The upstream entrypoint has CRLF line endings on Windows checkouts, so compose bypasses it.
  - A CUDA fault makes the server exit so Docker restarts it.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `409` on `/v1/audio/speech` | One request at a time. Retry with backoff, or use `/v1/audio/speech/warm`, which queues |
| `503` for ~15 s after a start | Still loading; poll `/health` |
| `port is already allocated` | The other runtime (or something else) is using 7860; stop it |
| Speech much slower than real time | Another process is using a lot of GPU memory, and Windows spills GPU memory into system RAM. Check `nvidia-smi`; free memory or stop the other workload |
| Engine crashed on an uploaded reference | Uploads go through ffmpeg; check the logs. Use a normal WAV or MP3 |
| `backend: CPU` in the logs | GPU not passed through, or a wrong `CUDA_ARCHS`; see step 3 |

## When you're done

Report to the user:
- the URL (`http://<server-ip>:7860`) and that `GET /usage` serves the API guide,
- which runtime is running,
- what you verified (health, a designed voice, the warm flow),
- anything you skipped (for example the firewall rule) and why.
