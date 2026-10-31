# Development-only image for measuring the model harness in the scorer's shape: linux/amd64, CPU only,
# --network none. Never used for scoring; the scoring Dockerfile at the repo root does not change.
#
#   docker build --platform linux/amd64 -f tools/docker/agent-bench.Dockerfile -t chameleonizer-agent .
#   docker run --rm --platform linux/amd64 --cpus 4 --memory 8g --network none \
#     -e MODEL=<model>.gguf -e MMPROJ=<mmproj>.gguf -v "$PWD/output:/app/output" \
#     chameleonizer-agent python -m tools.agree run --data bench/agree/v1 --out output/agree/amd64 --env-label amd64-cpu4
#
# Put the GGUF files in models/ at the repo root first (gitignored); docs/agentic-harness.md section 7
# gives the download command. The build fails if models/ is missing.

# --- Stage 1: llama-server, portable across x86 CPUs (Rosetta has AVX2, not AVX-512) -------------------
FROM --platform=linux/amd64 debian:bookworm-slim AS llama
ARG LLAMA_TAG=b12015
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${LLAMA_TAG}" https://github.com/ggml-org/llama.cpp /src
WORKDIR /src
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON \
        -DGGML_CPU_ALL_VARIANTS=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --config Release -j"$(nproc)" --target llama-server \
    && mkdir -p /opt/llama \
    && cp build/bin/llama-server /opt/llama/ \
    && find build \( -name '*.so' -o -name '*.so.*' \) -exec cp -P {} /opt/llama/ \;
# The shared libraries carry SOVERSIONs, so llama-server needs libllama.so.0 and friends, not only the
# unversioned names; -P keeps the version symlinks.

# --- Stage 2: the scoring image's contents, plus the bench tools, the server and the weights ------------
FROM --platform=linux/amd64 python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        git libgl1 libegl1 libglib2.0-0 fontconfig libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY chameleon_design_schema_v0.9.json .
COPY brand_kit/ ./brand_kit/
COPY renderer/ ./renderer/
COPY engine/ ./engine/
COPY tools/ ./tools/
COPY bench/ ./bench/
COPY input/ ./input/
COPY --from=llama /opt/llama/ /opt/llama/
COPY models/ /models/
ENV PATH="/opt/llama:${PATH}" LD_LIBRARY_PATH="/opt/llama" \
    CHAMELEON_AGENT=http CHAMELEON_AGENT_URL=http://127.0.0.1:8080/v1 CHAMELEON_AGENT_CONSTRAIN=grammar \
    CHAMELEON_AGENT_EXTRA='{"chat_template_kwargs":{"enable_thinking":false}}'
# Fail the build, not the first run, if the server cannot load its libraries.
RUN if ldd /opt/llama/llama-server | grep 'not found'; then exit 1; fi && llama-server --version
RUN mkdir -p /app/output && chmod +x /app/tools/docker/agent-entry.sh
ENTRYPOINT ["/app/tools/docker/agent-entry.sh"]
CMD ["python", "-m", "tools.agree", "doctor"]
