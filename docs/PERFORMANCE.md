# Speed and token efficiency

Goal: **1–3 s** for simple requests on 7B/14B on GPU, and multi-agent pipelines that cost as little
as possible. Here's what MyDevAgent already does and what you can tune.

## What's already on

| Technique | Where | Effect |
|---|---|---|
| Heuristic router (0 tokens, <1 ms) | `router.py` | most questions go to **fast = a single LLM call** |
| Formatter merged in fast | `graph.py → system_prompt(fused_delivery=True)` | no second call to "reformat" |
| Blackboard with per-agent `reads` | `state.py`, `agents.yaml` | each agent receives only the useful sections (−50/80% input tokens) |
| Stable prefix (persona → role) | `Team.system_prompt` | the server's KV/prefix cache reuses the persona across agents and requests |
| Per-agent `max_tokens` | `agents.yaml` | the quality gates answer in ~100–300 tokens |
| Specialists and gates in parallel | LangGraph `Send` | latency ≈ slowest agent, not the sum |
| `<think>` removed from the blackboard | `reasoning.strip_thinking` | the reasoning isn't passed on to other agents |
| Thinking only in deep and only for the `reasoning` tier | `modes.*.think` | `/no_think` (Qwen3) or `Reasoning: low` (gpt-oss) in the other modes |
| Search in fast without an LLM | `orchestrator.run` | results injected directly into the context |
| Pasted code doesn't inflate the mode | `router.prose_length` | a long traceback stays in fast |
| End-to-end streaming | CLI + SSE server | first token visible right away |

### Agent mode
| Technique | Effect |
|---|---|
| **Warmup** when the UI opens (1-token request in the background) | the first question doesn't pay 5–20 s of model loading |
| Fixed system prompt for the whole turn (persona → role → memory → repo map → rules → tools) | every loop step reuses the server's prefix cache |
| Tool output truncated in the middle (first/last lines) and windowed `read_file` | small context even on long files and logs |
| Older tool results emptied when the context exceeds `num_ctx × 3` characters | no context overflow on long tasks |
| Compact role prompt in agent mode | fewer tokens and less confusion for small models |
| "tests pass → stop" message after green tests | small models don't do pointless extra rounds |

## Measuring: `mydevagent bench`
```bash
mydevagent bench                     # first token and tokens/s for main and fast, with a profile suggestion
mydevagent bench --tiers main,reasoning
```
Example measured in the development container (CPU only, no GPU): `qwen2.5-coder:1.5b` → first token
0.5 s, ~14 tokens/s; `qwen2.5-coder:0.5b` → ~29 tokens/s. On an 8 GB GPU a 7B Q4 typically does
40–80 tokens/s. With CPU only, agent mode works but each step takes tens of seconds:
use `/fast`, a 3B model or a GPU.

## Server: the settings that matter most

### Ollama
```bash
export OLLAMA_FLASH_ATTENTION=1        # less memory and more speed on long contexts
export OLLAMA_KV_CACHE_TYPE=q8_0       # 8-bit KV cache: half the VRAM, ~identical quality
export OLLAMA_KEEP_ALIVE=30m           # the model stays loaded (no 5–20 s reload)
export OLLAMA_NUM_PARALLEL=3           # agents in parallel on the same model
export OLLAMA_CONTEXT_LENGTH=16384     # default context for models not created from a Modelfile
export OLLAMA_MAX_LOADED_MODELS=2      # main + fast together if VRAM allows
```
(Windows: set them as user environment variables and restart Ollama.)

### llama.cpp (the most efficient on small GPUs and Apple Silicon) — [`deploy/llamacpp.sh`](../deploy/llamacpp.sh)
- `--cache-reuse 256` → prefix reuse across requests
- `-hfd <draft 0.5B>` → **speculative decoding**: 1.5–2.5× on code (very predictable)
- `-ctk q8_0 -ctv q8_0`, `--flash-attn on`, `-np 3`

### vLLM (maximum throughput with many agents in parallel) — [`deploy/vllm.sh`](../deploy/vllm.sh)
- `--enable-prefix-caching`, `--speculative-config` with a 0.5B draft, AWQ/FP8 models

## Model choices

| Hardware | Recommendation | Why |
|---|---|---|
| CPU / 8 GB RAM | `qwen2.5-coder:3b` (main) | ~10–20 tok/s on a modern CPU |
| 8 GB GPU | `qwen2.5-coder:7b` Q4_K_M | ~40–80 tok/s, fits with 16k context |
| 12–16 GB GPU | `qwen2.5-coder:14b` | clear jump in quality, still fast |
| 24 GB GPU | `qwen3-coder:30b` (MoE, ~3B active) | 30B quality at ~3B speed |
| Apple Silicon 32 GB+ | `qwen3-coder:30b` with llama.cpp/MLX | unified memory holds the whole MoE |

Rules of thumb:
- **Same model for `main` and `reasoning`** if you don't have enough VRAM for two: swaps cost seconds.
- Quantization: Q4_K_M is the best trade-off; Q5_K_M/Q6_K if you have headroom; avoid < Q4 for code.
- Autocomplete: always a small **base** model (`qwen2.5-coder:1.5b-base`), never the team.
- Model names change fast: when a better open-weight coder comes out, just change
  the tag in `config/settings.yaml` (or `MYDEVAGENT_MODEL_MAIN=...`).

## Tuning in `config/settings.yaml`

| I want… | Change |
|---|---|
| more answers in fast | `router.fast_max_chars: 600` |
| always fast | `router.default_mode: fast` |
| fewer review rounds | `modes.balanced.max_review_rounds: 0` |
| fewer gates in deep | `modes.deep.gate: [security, reviewer]` |
| shorter contexts | `context.max_section_chars: 3000`, `context.history_turns: 2` |
| less output per agent | lower `max_tokens` in `agents.yaml` |
| no web page reading | `tools.web.fetch_top_n: 0` |

## Measuring
Every answer ends (CLI) with `mode · N agents · seconds · ~tokens`; the `agent_end` events
report ms and tokens per agent. Try the same request with `/fast` and `/deep` to see the cost
of each phase.
