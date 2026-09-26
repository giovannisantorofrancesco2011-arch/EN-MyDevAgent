#!/usr/bin/env bash
# vLLM (NVIDIA GPU, Linux/WSL2): maximum throughput with several agents in parallel.
#   pip install vllm
#   ./deploy/vllm.sh               → http://localhost:8001/v1
# Then in settings.yaml: default_backend: vllm  (the served names match the profile's)
set -euo pipefail
MODEL="${MODEL:-Qwen/Qwen2.5-Coder-7B-Instruct-AWQ}"        # 24GB: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8
SERVED="${SERVED:-qwen2.5-coder:7b}"                        # must match the profile's `main` tier
DRAFT="${DRAFT:-Qwen/Qwen2.5-Coder-0.5B-Instruct}"          # speculative decoding: same tokenizer

ARGS=(
  --port 8001
  --served-model-name "$SERVED"
  --max-model-len 16384
  --gpu-memory-utilization 0.90
  --enable-prefix-caching                 # reuses the KV cache of the shared prefix (persona + role)
  --enable-auto-tool-choice --tool-call-parser hermes   # function calling for native tools
)
if [[ "${SPECULATIVE:-1}" == "1" ]]; then
  ARGS+=(--speculative-config "{\"model\": \"$DRAFT\", \"num_speculative_tokens\": 5}")
fi
exec vllm serve "$MODEL" "${ARGS[@]}"
