# agent-land-breeze-tts-api

A self-hosted, streaming text-to-speech API for [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2), in Docker on an NVIDIA GPU. Built for AI agents and LAN apps that need a fast, consistent voice.

- **Streaming speech:** `POST /v1/audio/speech` streams raw 24 kHz PCM as it's generated.
- **Warm responses:** `POST /v1/audio/speech/warm` returns an id in milliseconds. `GET /response/{id}` plays it as WAV: it streams while generating, and holds playback back server-side until it can't catch up with generation. Kept for 24 h, and survives restarts.
- **Reusable voices:**
  - **Design:** describe a voice (`instruction` + `seed`) and get back a voice id.
  - **Reuse:** the first clip is saved and later requests clone from it.
  - **Cloning:** you can also clone from your own reference clips.
- **Two runtimes, one API:**
  - **GGUF (default):** Breeze-TTS-2.cpp, Q8_0 with the CUDA backend, using about 4–5 GB of GPU memory.
  - **PyTorch:** the original runtime, using about 17–18 GB, and faster.
- **One config file:** GPU, model, port and tuning are all in `.env`. `scripts/configure.py` detects the GPU and fills it in.
- **Self-documenting:** `GET /usage` serves [API.md](API.md).

> **Deploying with an AI agent?** Point it at **[AGENTS.md](AGENTS.md)**: a step-by-step deploy, adapt and verify guide.

## Quick start

```bash
git clone --recurse-submodules https://github.com/devdevgoat/agent-land-breeze-tts-api.git && cd agent-land-breeze-tts-api
pip install -U "huggingface_hub[hf_xet]" requests
python scripts/configure.py          # detect the GPU, write .env
python scripts/download_models.py    # fetch the model .env selects
docker compose up -d --build         # build + start (GGUF: ~5 min build, ~15 s start)
python client.py "Hello there." --instruction "A calm, friendly adult voice." -o hello.wav
```

## Performance (RTX 4090 24 GB, i9-12900K, Windows 11 + Docker Desktop/WSL2)

| Runtime | Time to first audio | Speed (RTF) | GPU memory | 50-word reply, whole file |
|---|---|---|---|---|
| GGUF Q8_0 (default, `--chunk-max 12`) | 0.33–0.75 s | 0.82–1.06 | 4.6 GB idle, 5.3 GB peak | ~15–19 s (voice-dependent) |
| GGUF Q8_0, `--chunk-max 40` | 0.34–0.8 s | 0.78–0.99 | same | ~13–15 s |
| PyTorch, fast mode | 0.1–0.5 s | 0.58–0.73 | ~17–18 GB | ~10–11 s (estimated from RTF) |
| PyTorch, eager | 0.65–2.8 s | 3.1–3.5 | ~9.4 GB | ~50 s (estimated from RTF) |

Warm responses played immediately start after about 2–4.5 s and didn't stall in testing on an otherwise idle GPU. **Sharing the GPU with another heavy job slows speech down 3–5×.** [BENCHMARKS.md](BENCHMARKS.md) has all the numbers, the models tested and the GPU-sharing results; `scripts/benchmark.py` measures your own hardware.

## Layout

| Path | What |
|---|---|
| `.env.example` → `.env` | **Every setting** you might change, commented: runtime, GPU, model, port, folders, tuning |
| `compose.yaml` | Both runtimes as Compose profiles; reads only `.env` |
| `runtimes/gguf/` | Default runtime: `Dockerfile` (Breeze-TTS-2.cpp at a pinned commit, CUDA), `compat_api.py` (the API layer), `run.sh` |
| `runtimes/pytorch/` | PyTorch runtime: `voice_api.py` + `upstream/` (BreezeBlue's inference code, a git submodule) |
| `scripts/` | `configure.py` (GPU detection), `download_models.py`, `benchmark.py`, `token_usage.py` |
| `benchmarks/results/` | Raw benchmark results (JSON) |
| `voices/` | Saved voices. Not committed; see [voices/README.md](voices/README.md) |
| `client.py` | Command-line client: streams to WAV, prints timings and the voice id |
| [API.md](API.md), [AGENTS.md](AGENTS.md), [BENCHMARKS.md](BENCHMARKS.md) | API reference, deploy guide, measurements |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [CITATION.cff](CITATION.cff), [TOKEN_LOG.md](TOKEN_LOG.md) | Licences, citations, build cost |

## Licence

- **This repo's code and docs:** [Apache-2.0](LICENSE).
- **Breeze TTS 2 weights, and speech you generate with them:** covered by BreezeBlue's **Research and Non-Commercial License**. This repo doesn't change that: no commercial use, and no unauthorized voice cloning or impersonation.
- **Everything else:** see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## How this was built

This repo was built by Claude (Opus 5.5) in Claude Code, working with the repo owner over one long session; the GGUF port was done by a subagent. [TOKEN_LOG.md](TOKEN_LOG.md) has the token and cost breakdown.
