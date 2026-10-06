# Breeze-TTS-2.cpp (GGUF, ggml CUDA backend) + the breeze-tts compatibility API.
#   Built by ../../compose.yaml (profile "gguf"); settings come from ../../.env.
#
# Stage 1 builds breeze-server/breeze-cli with ggml's CUDA backend for CUDA_ARCHS (89 = Ada).
# Vulkan is the upstream default, but there is no usable Vulkan driver inside a
# WSL2 container, so this image uses CUDA.
FROM nvidia/cuda:12.8.1-devel-ubuntu22.04 AS build

ARG BREEZE_CPP_REPO=https://github.com/HoppouAI/Breeze-TTS-2.cpp.git
ARG BREEZE_CPP_COMMIT=a0e177f91242ebbd2ea8d61548d742c89e4b9e06
ARG CUDA_ARCHS=89

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates build-essential ninja-build python3-pip \
    && pip3 install --no-cache-dir "cmake>=3.28" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone ${BREEZE_CPP_REPO} breeze \
    && cd breeze && git checkout ${BREEZE_CPP_COMMIT} \
    && git submodule update --init --recursive

WORKDIR /src/breeze
RUN cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DBREEZE_VULKAN=OFF -DBREEZE_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHS} \
        -DBREEZE_BUILD_SHARED=OFF -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
    && cmake --build build -j --target breeze-server breeze-cli \
    && mkdir -p /out \
    && cp "$(find build -type f -name breeze-server -perm -u+x | head -1)" /out/ \
    && cp "$(find build -type f -name breeze-cli -perm -u+x | head -1)" /out/ \
    && ldd /out/breeze-server && ls -la /out

FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip curl tini ffmpeg \
    && pip3 install "fastapi==0.115.*" "uvicorn==0.32.*" "httpx==0.27.*" "python-multipart==0.0.*" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /out/ /opt/breeze/bin/
ENV PATH=/opt/breeze/bin:$PATH LD_LIBRARY_PATH=/opt/breeze/bin
COPY compat_api.py run.sh /opt/breeze/
RUN sed -i 's/\r$//' /opt/breeze/run.sh && chmod +x /opt/breeze/run.sh

EXPOSE 7860
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/breeze/run.sh"]
