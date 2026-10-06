# Third-party notices and citations

This repository's own code and docs are licensed under Apache-2.0 (see [LICENSE](LICENSE)). It builds on the work below. **No third-party model weights, binaries or voice clips are included in this repository.** They are downloaded or built on your machine, under their own licences.

## Model weights (downloaded, not redistributed)

| Component | Used by | Licence | Source |
|---|---|---|---|
| Breeze TTS 2 (weights, audio tokenizer, configs) | PyTorch runtime | **BreezeBlue Research and Non-Commercial License** | https://huggingface.co/BreezeBlue/Breeze-TTS-2 ([licence](https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/main/LICENSE)) |
| Breeze TTS 2 GGUF conversion (`breeze-tts-2-q8_0.gguf`) | GGUF runtime | Same BreezeBlue licence (a derivative of the weights above) | https://huggingface.co/HoppouAI/Breeze-TTS-2.cpp |

**Important:** the BreezeBlue licence covers the model weights, checkpoints, adapters, derivative models **and self-hosted outputs**. They're for research and non-commercial use only, and the Apache-2.0 licence on this code does not change that. The licence also prohibits unauthorized voice cloning, impersonation and fraud; you are responsible for having the rights and consents for any reference audio and voices you use. BreezeBlue offers commercial use of outputs only through its paid hosted platform at https://breezeblue.ai.

## Code fetched or built by this repo

| Component | How it's used | Licence | Source |
|---|---|---|---|
| breeze-tts (BreezeBlue inference code) | Git submodule `src/`, built into the PyTorch image | Apache-2.0 | https://github.com/breezeblue-ai/breeze-tts |
| Qwen3-TTS audio tokenizer code (Alibaba Qwen Team) | Part of the Breeze inference stack (`qwen-tts` package) | Apache-2.0 | https://github.com/QwenLM/Qwen3-TTS |
| Breeze-TTS-2.cpp | Cloned at a pinned commit and compiled by `gguf/Dockerfile` | Apache-2.0 | https://github.com/HoppouAI/Breeze-TTS-2.cpp |
| ggml | Compiled into `breeze-server` (CUDA backend) | MIT | https://github.com/ggml-org/ggml |
| shine (MP3 encoder, a submodule of Breeze-TTS-2.cpp) | Compiled into `breeze-server` by `gguf/Dockerfile` | **LGPL-2.0** | https://github.com/toots/shine |
| FlashAttention 2.8.3 | Built into the PyTorch image | BSD-3-Clause | https://github.com/Dao-AILab/flash-attention |
| PyTorch (`pytorch/pytorch` base image) | PyTorch runtime | BSD-3-Clause | https://github.com/pytorch/pytorch |
| NVIDIA CUDA container images | Base images for both runtimes | NVIDIA Deep Learning Container License | https://catalog.ngc.nvidia.com/ |
| transformers | PyTorch runtime | Apache-2.0 | https://github.com/huggingface/transformers |
| FastAPI | Both API layers | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | Both API layers | BSD-3-Clause | https://github.com/encode/uvicorn |
| HTTPX | GGUF compatibility layer | BSD-3-Clause | https://github.com/encode/httpx |
| python-multipart | Form uploads | Apache-2.0 | https://github.com/Kludex/python-multipart |
| FFmpeg (Ubuntu package) | Converts uploaded reference audio | LGPL-2.1+ / GPL-2.0+ (distribution build) | https://ffmpeg.org/legal.html |

**Distributing built images:** if you distribute the built `breeze-tts-gguf` image (not just this source), it contains the statically linked LGPL-2.0 `shine` encoder and a GPL-licensed FFmpeg build. The obligations of those licences then apply, such as offering the corresponding source and allowing relinking. Building and running locally, as this repo does, triggers no distribution obligations.

## Citations

If you use this work, please credit the upstream projects:

- **Breeze TTS 2:** BreezeBlue. *Breeze TTS 2* (open-weight text-to-speech model), 2026. Model card: https://huggingface.co/BreezeBlue/Breeze-TTS-2. Announcement: https://breezeblue.ai/breeze-tts-2. Code: https://github.com/breezeblue-ai/breeze-tts.
- **Qwen3-TTS:** Alibaba Qwen Team. *Qwen3-TTS*. https://github.com/QwenLM/Qwen3-TTS.
- **Breeze-TTS-2.cpp:** HoppouAI. *Breeze-TTS-2.cpp: Breeze TTS 2 inference in C++ on ggml*. https://github.com/HoppouAI/Breeze-TTS-2.cpp.
- **ggml:** ggml-org. *ggml: Tensor library for machine learning*. https://github.com/ggml-org/ggml.

[CITATION.cff](CITATION.cff) has the same references in machine-readable form.
