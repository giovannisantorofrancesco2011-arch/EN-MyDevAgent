# Deploying with Ollama (recommended to get started)

```bash
# 1. install: https://ollama.com/download  (Linux: curl -fsSL https://ollama.com/install.sh | sh)
# 2. models for the gpu8 profile (change the tags for other profiles, see config/settings.yaml)
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:1.5b
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:1.5b-base      # autocomplete for Continue (optional)
ollama pull qwen2.5vl:7b                 # screenshots/mockups (optional)
# 3. "single-agent" model with the MyDevAgent persona built in
ollama create mydevagent -f modelfiles/Modelfile.gpu8
ollama run mydevagent
```

Recommended variables (see `docs/PERFORMANCE.md`): `OLLAMA_FLASH_ATTENTION=1`,
`OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_KEEP_ALIVE=30m`, `OLLAMA_NUM_PARALLEL=3`,
`OLLAMA_CONTEXT_LENGTH=16384`.

The 15-agent team uses Ollama through the OpenAI-compatible endpoint `http://localhost:11434/v1`
(default in `settings.yaml`). Everything in Docker: `docker compose -f deploy/docker-compose.yml up -d`.
