# Benchmarks

Everything measured while building this repo (2026-10-04 to 2026-10-06), on one machine. To add results for your hardware, run `python scripts/benchmark.py --label "<gpu, runtime>" --notes "<conditions>"` on the server. It writes `benchmarks/results/<date>_<gpu>_<runtime>.json`; add a row below.

**How to read these:**
- **RTF** (real-time factor) = generation time ÷ audio length. Below 1.0 is faster than real time.
- **TTFA** = time to first audio.
- **GPU memory** is for the whole card, from `nvidia-smi`.

## Test system

| | |
|---|---|
| GPU | NVIDIA GeForce RTX 4090, 24 GB (24,564 MiB), compute capability 8.9, 450 W limit, PCIe 4.0 x16 |
| Driver | 591.86 (reports CUDA 13.1); containers use CUDA 12.8 images |
| CPU | Intel Core i9-12900K, 16 cores / 24 threads |
| RAM | 32 GB (Docker VM: 16 CPUs, ~31 GB) |
| OS | Windows 11 Pro (build 26200), Docker Desktop 28.4.0 on WSL2 (kernel 6.18), WSL 3.0.1 |

## Models and runtimes tested

| Runtime | Model | Settings | Tested |
|---|---|---|---|
| PyTorch (upstream breeze-tts at `58ec70c`, torch 2.9.1, FlashAttention 2.8.3 built for sm80) | `BreezeBlue/Breeze-TTS-2`, bf16 (3.5 B params, 7.2 GB) | `--fast-all` (CUDA graphs) | yes |
| PyTorch | same | eager (`--no-fast-all`) | yes |
| GGUF: Breeze-TTS-2.cpp at `a0e177f`, ggml CUDA backend, built for sm89 | `breeze-tts-2-q8_0.gguf` (3.57 GB) | `--chunk-max 40` | yes |
| GGUF | same | `--chunk-max 12` (current default) | yes |
| GGUF, official prebuilt Windows **Vulkan** release (native, not Docker) | `breeze-tts-2-q8_0.gguf` | default | briefly (speed only) |
| GGUF | `f16`, `q6_k`, `q4_k`, `*-dd*` variants | | **not tested** |

## Summary

| Runtime / setting | TTFA | RTF | GPU memory | Startup | Image build |
|---|---|---|---|---|---|
| **PyTorch fast mode** | ~100 ms; 250–500 ms with a saved voice | **0.58–0.73** | ~17–18 GB | ~1.5–2.5 min (CUDA graph warmup) | ~25 min (FlashAttention compile) |
| PyTorch eager | 650 ms–2.8 s | 3.1–3.5 (slower than real time) | ~9.4 GB | ~1 min | same image |
| **GGUF Q8_0, chunk-max 40** | 340–800 ms; 0.9–1.2 s on a voice's first use | 0.78–0.99 | 3.8 GB loaded, 4.6 idle, 5.3 peak | 15–20 s | ~5 min |
| **GGUF Q8_0, chunk-max 12** (default) | 330–750 ms | 0.82–1.06 (voice-dependent, see below) | same | 15–20 s | ~5 min |
| GGUF Q8_0, native Windows Vulkan | not measured | ~0.7–0.9 at cfg 4 | not measured | | prebuilt |

GGUF speed is limited by many small sequential GPU steps (dispatch-bound), not raw compute. That's why the CUDA build in Docker and the native Vulkan build ended up close.

## Details

### PyTorch runtime (fast mode)

| Test | TTFA | Audio | Wall | RTF |
|---|---|---|---|---|
| Design voice, short line, first request | 149 ms | 6.6 s | 4.4 s | 0.68 |
| Reuse the same designed voice | 469 ms | 5.2 s | 3.8 s | 0.73 |
| Saved voice `voice=<id>` | 204–379 ms | 1.3–4.9 s | | 0.68–0.84 |
| Long text, 602 characters | 276 ms | 33.8 s | 22.1 s | 0.66 |
| Long text, 1,205 characters | 509 ms | 73.2 s | 47.8 s | 0.65 |
| Voice clone, built from `compose.yaml` (2026-10-06) | 432 ms | 4.2 s | 2.5 s | 0.58 |

Eager mode, 4 requests: TTFA 643 ms–2.8 s, RTF 3.13–3.54, 9.4 GB.

### GGUF Q8_0

| Test (chunk-max 40) | TTFA | RTF |
|---|---|---|
| Saved voice + instruction, cfg 4 | 360–620 ms (0.9–1.2 s on a voice's first use, while its reference is encoded) | 0.90–0.97 |
| Designed voice, first request (created and saved) | 340–360 ms | 0.90–0.97 |
| Designed voice, reused | 350–800 ms | 0.78–0.99 |
| Upload clone (`ref_audio` + `ref_text`) | ~890 ms | 0.83 |
| Long text, 26.7 s of audio | 720 ms | 0.88 |

**Whole-file time for a 50-word (53-word) reply**, waiting for the complete file:

| Setting | Voice | Audio | Time to complete file | RTF |
|---|---|---|---|---|
| chunk-max 40 | saved voice (energetic) | 16.0 s | 13.3 / 13.8 / 15.3 s | 0.83–0.96 |
| chunk-max 40 | designed voice | 15.8–16.5 s | 13.7 / 14.7 s | 0.86–0.89 |
| chunk-max 12 | saved voice (energetic) | 16.0 s | 15.2 / 15.3 s | 0.95–0.96 |
| chunk-max 12 | designed "calm" voice | 19.4 s | 19.3 s (uncontended run) | 0.99 |

**Voice matters:**
- **Speaking rate:** a calm voice speaks more slowly (19.4 s for the same 53 words, about 0.37 s per word against about 0.30).
- **Speed:** it also generated nearer real time (RTF about 1.0).
- **What the server does about it:** it estimates each saved voice's speaking rate from its reference clip and transcript to size the warm-response head start.

### Warm responses (`/v1/audio/speech/warm` + `/response/{id}`)

A simulated player opened `/response/{id}` right after the warm call and played in real time. The **margin** is the least audio the player had buffered at any point; a negative margin means it would have stalled.

| Version | Wait before first audio | Min margin | Result |
|---|---|---|---|
| chunk-max 40, fixed 0.75 s head start | 0.7–2.6 s | +0.10 / +0.12 / −0.14 s | one stall |
| chunk-max 12, head start covers one chunk at the live rate | 1.2–2.3 s | +0.77 / +0.78 / +1.72 s | no stalls |
| Same, second request queued behind the first | 16.2–16.5 s (queued) | +0.90 / +0.91 / +0.92 s | no stalls |
| Benchmark script, calm voice, before the per-voice estimate | 1.4–1.5 s | +0.76 / **−9.93 s** | one stall: that run slowed to RTF 1.54 mid-playback because of GPU contention |
| Benchmark script, after the per-voice estimate and length-scaled safety | 1.3–6.6 s (adapts to the live rate) | +0.11 / +0.81 / +3.99 s | no stalls |

**Engine restart:** a warm request sent while the engine was restarting waited about 20 s for it, then played normally; before the fix it returned a 500.

**The limit of this design:** the head start is chosen before playback starts. A slowdown that begins **after** playback has started (another process taking the GPU) can still cause a stall. Avoid sharing the GPU with heavy jobs, or ask for a bigger head start with `?min_lead=`.

### Benchmark script run (2026-10-06, `benchmarks/results/2026-10-06_nvidia-geforce-rtx-4090_gguf.json`)

GGUF Q8_0, chunk-max 12, built from this repo's `compose.yaml`. **Shared GPU:** a second, production instance on the same card was serving live warm requests during the run.

| Test | TTFA | Audio | RTF |
|---|---|---|---|
| Short line (×4) | 0.40–0.75 s | 5.8 s | 0.96–1.06 |
| 50 words (×4) | 0.34–1.07 s | 19.4 s | 0.99 uncontended; 1.58 / 1.61 / 2.87 while the other instance was busy |
| Warm (×3) | id in 12–22 ms; first audio after 1.3–6.6 s | 19.4 s | margins +0.11 to +3.99 s, no stalls |

GPU memory: 9.2 GB idle and 11.3 GB peak for both instances together, so about 4–6 GB for this one.

## Sharing the GPU with other workloads

| Situation | Effect on TTS |
|---|---|
| TTS alone | RTF 0.82–0.91, TTFA 330–530 ms |
| Image generation (Z-Image Turbo, bf16, about 14–18 GB) rendering a sprite sheet at the same time | RTF **3.6–5.0**, TTFA up to 4 s; GPU at 23.8 / 24.6 GB. Back to normal as soon as the image job ends |
| Image generation with the GGUF 3-bit model, weights moved per job | about 1.6× slower during image bursts |
| A second Breeze GGUF instance (about 5 GB) | Fine while idle; both slow down when both generate (about 1.6–2.9×) |
| A PyTorch Breeze instance **starting up** next to the GGUF engine | Crashed the GGUF engine (`CUDA error: unspecified launch failure`). Its container restarted in about 15 s, but warm requests failed in the meantime |

**The cause:** when total GPU memory goes past 24 GB, Windows/WSL2 spills GPU memory into system RAM instead of failing. Everything on the card then slows down several times. Keep total use well under the card's capacity. Disabling *CUDA - Sysmem Fallback Policy* in the NVIDIA Control Panel turns this into fast out-of-memory errors instead.

## Build times

| | Time |
|---|---|
| PyTorch image (compiles FlashAttention 2.8.3 from source, `MAX_JOBS=8`) | ~25 min (the FlashAttention wheel alone ~24.5 min) |
| GGUF image (compiles Breeze-TTS-2.cpp + ggml CUDA for one architecture) | ~5 min |
| Weights download | 7.2 GB (PyTorch) in ~2 min; 3.57 GB (GGUF) |
