# Customization

Everything is configuration + plain-text prompts: no code to touch for 90% of changes.

## 1. Models and profiles
`config/settings.yaml → profiles`. Each tier (`main`, `fast`, `reasoning`, `vision`, `embed`) accepts a
tag or `{model, backend}` to mix servers:

```yaml
profiles:
  my-pc:
    main: { model: "qwen3-coder:30b", backend: vllm }      # code on vLLM
    fast: "qwen2.5-coder:1.5b"                              # router/queries on Ollama
    reasoning: "qwen3:14b"                                  # thinking only in deep
    embed: "nomic-embed-text"
    num_ctx: 32768
    native_tools: true
```
Then `MYDEVAGENT_PROFILE=my-pc` (or `profile: my-pc`). Quick override without files:
`MYDEVAGENT_MODEL_MAIN=mydevagent-custom mydevagent chat`.

## 2. Changing an agent
- **Behavior**: edit `prompts/agents/NN_name.md` (English = best results with coder models).
- **Budget**: `max_tokens`, `temperature`, `tier` in `config/agents.yaml`.
- **What it sees**: `reads` (fewer sections = faster).
- **When it activates**: `keywords` (IT/EN) and `aliases` (`@name`).
- **Global rules**: `prompts/system_persona.md` (applies to everyone; keep it short: it's in the prefix of every
  call).

## 2b. Project memory, custom commands, permissions
- **`MYDEVAGENT.md`** in the root: commands (`- test: pytest -q`), architecture, conventions. The agent
  reads it on every request; `/init` generates it, `#note` adds lines. It also reads `AGENTS.md`, `CLAUDE.md` and
  `~/.mydevagent/MYDEVAGENT.md` (personal preferences for all projects).
- **Custom commands**: `.mydevagent/commands/<name>.md` → `/name` (see `docs/TUI.md`).
- **Permissions**: `mydevagent --permissions auto-edit`; "always allow" rules in
  `.mydevagent/settings.json`, for example:
  ```json
  { "allow": ["bash:pytest*", "bash:npm test*", "bash:ruff*", "edit:src/*"] }
  ```

## 3. Groups (teams) and modes
Modes are ready-made groups of agents. Create your own combinations in `settings.yaml`:

```yaml
modes:
  balanced:
    gate: [reviewer, security]      # every task also goes through security
    max_review_rounds: 1
  deep:
    gate: [security, performance, edge_cases, reviewer]
    max_review_rounds: 3
    think: true
    docs: true
```
On-the-fly teams right in the message: `@be @db @sec create the payment endpoint`.

## 4. Replacing an agent with another role
The system requires **exactly 15 core agents** (`agents.yaml`) and **20 extended ones** for `/ultra-deep`
(`agents_ultra.yaml`, ids 16-35), validated at startup. To change a role, for example
turning *DevOps* into *Mobile (iOS/Android)*:
1. in `agents.yaml` change `key`, `name`, `role`, `goal`, `keywords`, `aliases`, `prompt` of agent 8;
2. create `prompts/agents/08_mobile.md` starting from an existing specialist prompt;
3. `stage: specialist` → it automatically joins the parallel fan-out.

Available stages: `research`, `plan`, `specialist` (fan-out), `test`, `gate` (quality gate with VERDICT),
`docs`, `final`. For more than 15 agents: `load_registry(settings, strict=False)` in your code.

## 5. Custom tools
Create a Python module (e.g. `my_tools/jira.py`) and register it:

```python
# my_tools/jira.py
import os, httpx
from mydevagent.tools import tool

@tool("jira_issue", "Read a Jira issue (summary, description, status).",
      {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]})
def jira_issue(ctx, key: str) -> str:
    r = httpx.get(f"{os.environ['JIRA_URL']}/rest/api/3/issue/{key}",
                  auth=(os.environ["JIRA_USER"], os.environ["JIRA_TOKEN"]), timeout=10)
    r.raise_for_status()
    f = r.json()["fields"]
    return f"{key}: {f['summary']} [{f['status']['name']}]\n{f.get('description')}"
```

```yaml
# settings.yaml
tools:
  custom: ["my_tools.jira"]
```
```yaml
# agents.yaml → the agent that should use it
    tools: [rag_search, read_file, jira_issue]
```
Native tools are called by the model (function calling) when the profile has `native_tools: true`
(recommended from 14B up; 7B models are less reliable at function calling). The `ctx` context provides
`ctx.settings`, `ctx.workspace`, `ctx.web`, `ctx.sandbox`, `ctx.index`, `ctx.llm`.
Tools always return strings; errors and exceptions are converted to `ERROR: …` without
stopping the team.

Built-in tools: `web_search`, `web_fetch`, `read_file`, `list_dir`, `grep`, `write_file` (disabled by
default), `git_status`, `git_diff`, `git_log`, `git_commit` (disabled by default), `run_code`, `rag_search`.

## 6. Knowledge of your projects without training: RAG
```bash
cd ~/code/my-project
mydevagent index            # embeddings with nomic-embed-text (automatic lexical fallback)
mydevagent chat             # the agents receive the relevant code snippets
```
Run `mydevagent index` again after big changes. It's the cheapest and most effective way to make the model
"know" your code — try this **before** fine-tuning.

## 7. Lightweight fine-tuning (QLoRA) on your style
Useful when you want the model to write *like you* (conventions, internal libraries, recurring patterns).

```bash
pip install -e ".[finetune]"                                   # NVIDIA GPU (Linux/WSL2) or Colab
python finetune/build_dataset.py ~/code/project1 ~/code/project2 --extra finetune/my_qa.jsonl
python finetune/train_qlora.py --config finetune/config_qlora.yaml
bash finetune/export_gguf.sh                                    # → ollama model "mydevagent-custom"
MYDEVAGENT_MODEL_MAIN=mydevagent-custom mydevagent chat
```
- Dataset: *before/after* pairs from your commits (commit message = instruction), FIM examples for
  autocomplete and your own question/answer pairs. Files with secrets are discarded automatically.
- 7B QLoRA: ~10 GB VRAM, 1–3 hours for a few thousand examples on an RTX 3060/4070.
- Start with 500–3000 good-quality examples and 1–2 epochs; always evaluate on `val.jsonl` and on real tasks:
  a bad fine-tune makes general reasoning worse.
- Details in [`finetune/README.md`](../finetune/README.md).

## 8. Language
Agent prompts are in English, and answers come in the user's language (a rule in the persona). To always force
a specific language: add to `system_persona.md` → `Always reply in English.` (or any other language).
