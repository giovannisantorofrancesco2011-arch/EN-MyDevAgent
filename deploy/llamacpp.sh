#!/usr/bin/env bash
# llama.cpp llama-server: the lightest and most efficient on small GPUs, Apple Silicon and CPU.
#   brew install llama.cpp   |   winget install llama.cpp   |   build from https://github.com/ggml-org/llama.cpp
#   ./deploy/llamacpp.sh      → http://localhost:8080/v1   (settings.yaml: default_backend: llamacpp)
set -euo pipefail
MAIN="${MAIN:-Qwen/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M}"
DRAFT="${DRAFT:-Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF:Q8_0}"
ALIAS="${ALIAS:-qwen2.5-coder:7b}"   # exposed name: must match the `main` tier

exec llama-server \
  -hf "$MAIN" \
  --alias "$ALIAS" \
  --port 8080 \
  -c 16384 \
  -ngl 99 \
  --flash-attn on \
  -ctk q8_0 -ctv q8_0 \
  --cache-reuse 256 \
  -np 3 \
  --jinja \
  -hfd "$DRAFT" --draft-max 16 --draft-min 4
# Options:
#   -ngl 99           all layers on GPU (lower it if VRAM isn't enough; 0 = CPU only)
#   --flash-attn on   on older builds just use -fa
#   -ctk/-ctv q8_0    quantized KV cache: half the memory, practically identical quality
#   --cache-reuse     reuses the cached prefix across requests (persona + role prompt)
#   -np 3             3 parallel slots (agents in parallel)
#   -hfd ...          draft model for speculative decoding (1.5–2.5x faster on code)
#   --jinja           official chat template → function calling for native tools
