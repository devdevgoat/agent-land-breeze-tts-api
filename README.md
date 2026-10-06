# agent-land-breeze-tts-api

A self-hosted, streaming text-to-speech API for [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2), in Docker on an NVIDIA GPU. Built for AI agents and LAN apps that need a fast, consistent voice.

- **Streaming speech:** `POST /v1/audio/speech` streams raw 24 kHz PCM as it's generated.
- **Warm responses:** `POST /v1/audio/speech/warm` returns an id in about 35 ms. `GET /response/{id}` plays it as WAV: it streams while generating, and holds playback back server-side so it can't catch up with generation. Kept for 24 h.
- **Reusable voices:**
  - **Design:** describe a voice (`instruction` + `seed`) and get back a voice id.
  - **Reuse:** the first clip is saved and later requests clone from it, so the voice stays the same for any text.
  - **Cloning:** you can also clone from your own reference clips.
- **Two runtimes, one API:**
  - **GGUF (default):** Breeze-TTS-2.cpp, Q8_0 with the CUDA backend, using about 4–5 GB of GPU memory.
  - **PyTorch:** the original runtime, using about 17–18 GB.
- **Self-documenting:** `GET /usage` serves [API.md](API.md).

> **Deploying with an AI agent?** Point it at **[AGENTS.md](AGENTS.md)**: a step-by-step deploy-and-verify guide.

## Quick start (GGUF runtime)

```bash
git clone https://github.com/devdevgoat/agent-land-breeze-tts-api.git && cd agent-land-breeze-tts-api
python3 -c "from huggingface_hub import hf_hub_download as d; d('HoppouAI/Breeze-TTS-2.cpp', 'breeze-tts-2-q8_0.gguf', local_dir='models/gguf')"
CUDA_ARCHS=89 docker compose -f gguf/docker-compose.yml up -d --build    # 89 = RTX 40xx; see AGENTS.md
curl http://127.0.0.1:7860/health
curl -X POST http://127.0.0.1:7860/v1/audio/speech -F "text=Hello there." \
  -F "instruction=A calm, friendly adult voice." -F "cfg_scale=4" --output hello.pcm
```

## Performance (RTX 4090, GGUF Q8_0)

| | |
|---|---|
| Time to first audio | ~0.35–0.9 s |
| Generation speed | ~0.85–0.95× real time (a 50-word reply, 16 s of audio, takes ~14–15 s) |
| Warm response, played immediately | first audio after ~1.2–2.3 s, no stalls in testing |
| GPU memory | ~4.6 GB idle, ~5.3 GB peak |

Generation is only slightly faster than real time. That's why the warm endpoint holds back the first bytes until playback can't catch up. Another heavy GPU job running at the same moment can slow speech down several times.

## Layout

| Path | What |
|---|---|
| `gguf/` | Default runtime: `Dockerfile` (builds Breeze-TTS-2.cpp at a pinned commit), `compat_api.py` (the API layer), `run.sh`, `docker-compose.yml` |
| `docker-compose.yml`, `server/voice_api.py`, `src/` | Original PyTorch runtime; `src/` is the upstream inference code as a git submodule |
| `voices/` | Saved voices (`<id>.wav` + `.txt` + optional `.json`). Not committed; see [voices/README.md](voices/README.md) |
| `client.py` | Command-line client: streams to WAV, prints timings and the voice id |
| [API.md](API.md) | Full API reference |
| [AGENTS.md](AGENTS.md) | Deployment walkthrough for AI agents |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [CITATION.cff](CITATION.cff) | Licences and citations of everything this builds on |
| [TOKEN_LOG.md](TOKEN_LOG.md), `scripts/token_usage.py` | What it cost in Claude tokens to build this repo, and the script that computes it |

## Licence

- **This repo's code and docs:** [Apache-2.0](LICENSE).
- **Breeze TTS 2 weights, and speech you generate with them:** covered by BreezeBlue's **Research and Non-Commercial License**. This repo doesn't change that: no commercial use, and no unauthorized voice cloning or impersonation.
- **Everything else:** see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## How this was built

This repo was built by Claude (Opus 5.5) in Claude Code, working with the repo owner over one long session. That covered the PyTorch setup, the API, voice reuse, the GGUF port (done by a subagent), and the warm-response endpoint. [TOKEN_LOG.md](TOKEN_LOG.md) has the token and cost breakdown.
