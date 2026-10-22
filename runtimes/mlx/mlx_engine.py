"""MLX inference engine for Breeze TTS 2 on Apple Silicon.

Wraps breeze_tts_mlx to provide a simple generate() interface that yields
streaming PCM chunks.  The model runs on Metal GPU via MLX; the audio
codec (Qwen3 tokenizer) runs on PyTorch MPS.

Usage:
    engine = MLXEngine("path/to/Gust-MLX")
    await engine.warmup()
    async for chunk in engine.generate("Hello!", instruction="A calm voice."):
        # chunk: bytes (s16le 24 kHz mono PCM)
        ...
"""

from __future__ import annotations

import asyncio
import os
import struct
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24000


@dataclass
class EngineConfig:
    max_new_tokens: int = 1500
    max_seq_len: int = 2048
    repetition_penalty: float = 1.1
    codec_chunk_frames: int = 2
    temperature: float = 0.9
    top_k: int = 50
    top_p: float = 1.0
    compile: bool = True
    fast_first: int = 6
    cache_gb: float = 1.0


def _config_from_env() -> EngineConfig:
    return EngineConfig(
        max_new_tokens=int(os.environ.get("BREEZE_MAX_NEW_TOKENS", "1500")),
        compile=os.environ.get("BREEZE_MLX_COMPILE", "1") == "1",
        fast_first=int(os.environ.get("BREEZE_MLX_FAST_FIRST", "6")),
        cache_gb=float(os.environ.get("BREEZE_MLX_CACHE_GB", "1")),
    )


class MLXEngine:
    """Loads a Breeze TTS 2 MLX checkpoint and generates streaming audio."""

    def __init__(self, model_path: str | Path, seed: int = 42) -> None:
        self.model_path = Path(model_path)
        self.seed = seed
        self.config = _config_from_env()
        self._runtime = None
        self._loaded = False

    def load(self) -> None:
        """Load the model into memory.  Call once at startup."""
        import mlx.core as mx
        from breeze_tts_mlx.runtime import BreezeMLXRuntime, MLXRuntimeConfig
        from breeze_tts_mlx.sampling import SamplingConfig

        if self.config.cache_gb > 0:
            mx.set_cache_limit(int(self.config.cache_gb * 1024 ** 3))

        sampling = SamplingConfig(
            temperature=self.config.temperature,
            top_k=self.config.top_k,
            top_p=self.config.top_p,
            do_sample=True,
        )
        runtime_config = MLXRuntimeConfig(
            max_new_tokens=self.config.max_new_tokens,
            max_seq_len=self.config.max_seq_len,
            repetition_penalty=self.config.repetition_penalty,
            codec_chunk_frames=self.config.codec_chunk_frames,
            backbone_sampling=sampling,
            depth_sampling=sampling,
        )
        self._runtime = BreezeMLXRuntime(
            str(self.model_path),
            audio_device="auto",
            config=runtime_config,
            seed=self.seed,
        )
        self._loaded = True
        print(f"MLX engine loaded from {self.model_path}", flush=True)

    async def warmup(self) -> None:
        """Run a dummy generation to trigger Metal shader compilation."""
        if not self._loaded:
            self.load()
        print("MLX engine warm-up: compiling Metal shaders...", flush=True)
        t0 = time.monotonic()
        chunks = 0
        async for _ in self.generate("Hello.", seed=0):
            chunks += 1
        elapsed = time.monotonic() - t0
        print(f"MLX engine warm-up done: {chunks} chunks in {elapsed:.1f}s", flush=True)

    def _select_template(
        self,
        instruction: str | None,
        ref_audio_path: str | None,
    ) -> str:
        if ref_audio_path and instruction:
            return "ref_edit_tata"
        if ref_audio_path:
            return "ref_clone_tata"
        if instruction:
            return "tts_instruction"
        return "tts_plain"

    async def generate(
        self,
        text: str,
        *,
        instruction: str | None = None,
        cfg_scale: float = 1.0,
        seed: int = 42,
        ref_audio_path: str | None = None,
        ref_text: str = "",
    ) -> AsyncIterator[bytes]:
        """Generate speech and yield s16le 24 kHz mono PCM chunks."""
        if not self._loaded:
            raise RuntimeError("Engine not loaded. Call load() first.")

        from breeze_tts_mlx.templates import get_template, prepare_inputs

        template_name = self._select_template(instruction, ref_audio_path)
        request: dict = {
            "id": "req",
            "text": text,
            "speaker": "S0",
        }
        if instruction:
            request["instruction"] = instruction
        if ref_audio_path:
            request["ref_audio_path"] = ref_audio_path
            request["ref_text"] = ref_text

        inputs = prepare_inputs(
            self._runtime.tokenizer,
            self._runtime.audio_tokenizer,
            self._runtime,
            [request],
            get_template(template_name),
            guidance_scale=cfg_scale,
            guidance_scale_ref=None,
            guidance_scale_ins=None,
        )

        self._runtime.seed = seed
        loop = asyncio.get_running_loop()

        gen = self._runtime.iter_audio_chunks(inputs, request_id="req")
        while True:
            chunk = await loop.run_in_executor(None, lambda: next(gen, None))
            if chunk is None:
                break
            audio = chunk.audio
            if isinstance(audio, np.ndarray):
                pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            else:
                pcm = audio
            if pcm:
                yield pcm

    @property
    def ready(self) -> bool:
        return self._loaded
