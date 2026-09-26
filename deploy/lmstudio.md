# Deploying with LM Studio (GUI, Windows/macOS/Linux)

1. Download LM Studio from https://lmstudio.ai and search for **Qwen2.5-Coder-7B-Instruct** (GGUF Q4_K_M; on Mac
   you can pick the MLX version, faster on Apple Silicon). Also download
   **Qwen2.5-Coder-1.5B-Instruct** and **nomic-embed-text-v1.5**.
2. **Developer** tab → **Start Server** (port 1234). In the model settings: Context Length 16384,
   Flash Attention ON, and (if available) *Speculative Decoding* with the 1.5B/0.5B model as the draft.
3. Copy the model **identifiers** shown by LM Studio (e.g. `qwen2.5-coder-7b-instruct`).
4. Configure MyDevAgent (`.env`):
   ```bash
   LLM_BASE_URL=http://localhost:1234/v1
   LLM_API_KEY=lm-studio
   MYDEVAGENT_MODEL_MAIN=qwen2.5-coder-7b-instruct
   MYDEVAGENT_MODEL_REASONING=qwen2.5-coder-7b-instruct
   MYDEVAGENT_MODEL_FAST=qwen2.5-coder-1.5b-instruct
   MYDEVAGENT_MODEL_EMBED=text-embedding-nomic-embed-text-v1.5
   ```
   or in `settings.yaml`: `default_backend: lmstudio` and the names in the profile.
5. `mydevagent doctor` → checks that the models show up; then `mydevagent chat`.

System prompt for direct use in LM Studio (without the orchestrator): copy the `SYSTEM` block of
`modelfiles/Modelfile.gpu8` into the *System Prompt* field.
