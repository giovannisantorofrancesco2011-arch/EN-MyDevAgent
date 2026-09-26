#!/usr/bin/env bash
# MyDevAgent — quick install (Linux/macOS).  Usage:  ./scripts/install.sh [cpu|gpu8|gpu16|gpu24]
# Without a profile it picks one based on the GPU.
set -euo pipefail
detect_profile() {  # same logic as `mydevagent doctor`: NVIDIA VRAM or Apple Silicon unified memory
  local gb=0
  if command -v nvidia-smi >/dev/null 2>&1; then
    gb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1 | awk '{printf "%d", $1/1024}')
  elif [[ "$(uname)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    gb=$(sysctl -n hw.memsize | awk '{printf "%d", $1/1073741824*0.7}')
  fi
  gb=${gb:-0}
  if (( gb >= 22 )); then echo gpu24; elif (( gb >= 14 )); then echo gpu16; elif (( gb >= 7 )); then echo gpu8; else echo cpu; fi
}
PROFILE="${1:-$(detect_profile)}"
[[ -n "${1:-}" ]] || echo "==> Profile detected from hardware: $PROFILE (to choose it yourself: ./scripts/install.sh gpu8)"
cd "$(dirname "$0")/.."

case "$PROFILE" in
  cpu)   MAIN="qwen2.5-coder:3b";  EXTRA="qwen2.5-coder:7b" ;;
  gpu8)  MAIN="qwen2.5-coder:7b";  EXTRA="" ;;
  gpu16) MAIN="qwen2.5-coder:14b"; EXTRA="" ;;
  gpu24) MAIN="qwen3-coder:30b";   EXTRA="" ;;
  *) echo "Unknown profile: $PROFILE (cpu|gpu8|gpu16|gpu24)"; exit 1 ;;
esac

echo "==> 1/5 Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  if [[ "$(uname)" == "Darwin" ]]; then
    echo "Install Ollama from https://ollama.com/download (or: brew install ollama) and run this again."; exit 1
  fi
  command -v zstd >/dev/null 2>&1 || echo "    note: the Ollama installer requires zstd (Debian/Ubuntu: sudo apt-get install zstd)"
  curl -fsSL https://ollama.com/install.sh | sh
fi
if ! curl -fsS http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "    starting 'ollama serve' in the background"
  (OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 nohup ollama serve >/tmp/ollama.log 2>&1 &)
  sleep 3
fi

echo "==> 2/5 Models ($PROFILE)"
for m in "$MAIN" "qwen2.5-coder:1.5b" "nomic-embed-text" $EXTRA; do
  ollama pull "$m"
done
ollama create mydevagent -f "modelfiles/Modelfile.$PROFILE"

echo "==> 3/5 Python environment"
PY="${PYTHON:-python3}"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[server,search]"

echo "==> 4/5 Configuration"
[[ -f .env ]] || cp .env.example .env
sed -i.bak "s/^MYDEVAGENT_PROFILE=.*/MYDEVAGENT_PROFILE=$PROFILE/" .env && rm -f .env.bak

echo "==> 5/5 Sandbox (optional, requires Docker)"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker pull -q python:3.12-slim >/dev/null && docker pull -q node:22-slim >/dev/null && echo "    sandbox images ready"
else
  echo "    Docker not available: sandbox tests will be skipped (everything else works)"
fi

mydevagent doctor || true
cat <<MSG

Done! Next steps:
  ./run.sh                        # starts MyDevAgent (also from another folder: /path/to/run.sh)
  source .venv/bin/activate
  mydevagent                      # interactive interface in the terminal
  mydevagent serve                # OpenAI-compatible server on http://127.0.0.1:8000/v1
  ollama run mydevagent           # direct single-agent model
MSG
