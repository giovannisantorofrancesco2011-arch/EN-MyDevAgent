# MyDevAgent

> This is the **English edition** of MyDevAgent (branch `EN-MyDevAgent`). The Italian edition lives on branch
> [MyDevAgent](https://github.com/giovannisantorofrancesco2011-arch/MyDevAgent/tree/MyDevAgent).

**Local-first coding assistant with 35 specialized agents** (15 in the core + 20 for
`/ultra-deep` mode). Everything runs on your PC (8 GB+ GPU or CPU only), works offline and, when you're
online, searches the web in real time. Like Claude Code, it **works directly on your project's files**:
it reads the code, edits it while showing you the diffs, runs the tests, fixes things, and you can undo it all with `/undo`.
You can use it from the terminal, as an OpenAI-compatible server (VS Code/Continue, Cursor, Aider, Cline…) or as a
"single-agent" Ollama model.

> **What it actually is.** It's not an LLM trained from scratch (that would take millions in GPUs).
> It's a complete system built on top of the best open-weight coding models (Qwen2.5-Coder,
> Qwen3-Coder, …): a **system persona**, **35 role agents orchestrated with LangGraph**, real **tools**
> (web search, Docker sandbox, filesystem, git, RAG over your codebase, vision), a **router** that
> keeps simple requests fast, and a **QLoRA setup** to train it on your projects.
> Quality depends on the model you pick: a local 7B won't match frontier cloud models,
> but the pipeline (plan → implementation → tests actually run → review) raises its reliability a lot.

```
request ─▶ router ─┬─ fast        the right specialist reads/edits/tests the files (tool loop)
                   ├─ balanced    Architect ▶ file agent ▶ tests ▶ Reviewer on the real diff ▶ fixes
                   ├─ deep        + Security, Performance, Edge cases (2 fix rounds)
                   └─ ultra-deep  35 agents: web research if needed ▶ requirements ▶ plan + devil's advocate
                                  ▶ test strategy ▶ agent ▶ tests ▶ 10 gates + 15 review lenses in parallel
                                  ▶ Integrator ▶ fixes (3 rounds) ▶ docs + release ▶ delivery
```

## The agents

**Core (15)** — used by fast / balanced / deep:

| # | Agent | # | Agent | # | Agent |
|---|---|---|---|---|---|
| 1 | Code Architect | 6 | Backend & API | 11 | Research (web) |
| 2 | Algorithms & Data Structures | 7 | Database & ORM | 12 | Code Reviewer & Refactor |
| 3 | Languages & Frameworks | 8 | DevOps & CI/CD | 13 | Documentation |
| 4 | Debugging & Testing | 9 | Security | 14 | Edge Case & Robustness |
| 5 | Frontend | 10 | Performance | 15 | Output Formatter |

**Extended (20)** — added in `/ultra-deep` (or when you call them with `@alias`):

| # | Agent | # | Agent |
|---|---|---|---|
| 16 | Requirements Analyst | 26 | Observability |
| 17 | API Designer | 27 | Dependencies & supply chain (web) |
| 18 | Mobile | 28 | Migrations & legacy |
| 19 | Cloud Architect | 29 | Test Strategist |
| 20 | Data Engineer | 30 | Threat modeling & privacy |
| 21 | AI/ML Engineer | 31 | Scalability & load |
| 22 | Concurrency & async | 32 | Release & versioning |
| 23 | Systems & low-level | 33 | Web fact-checker |
| 24 | Accessibility & i18n | 34 | Devil's advocate |
| 25 | UX/UI Designer | 35 | Lead Integrator |

Roles, prompts, tools, flow and interactions: **[docs/AGENTS.md](docs/AGENTS.md)**.

## What it can do (the best ideas from coding assistants)
| Feature | Inspired by | Where |
|---|---|---|
| Agent that reads, edits (search/replace edits), runs commands and tests in a loop | Claude Code, Codex CLI | `mydevagent/agent/` |
| Permissions: ask · auto-edit · plan · auto, `Shift+Tab`, "always allow" rules | Claude Code | `/permissions` |
| Inline diffs with confirmation, rejection with feedback, checkpoints, `/undo`, `/rewind` | Claude Code, Cursor | UI |
| Agent todo list, `Esc` to interrupt, notification when work is done | Claude Code | UI |
| Project memory `MYDEVAGENT.md` (also reads `AGENTS.md`/`CLAUDE.md`), `/init`, `#note` | Claude Code, Codex | `/memory` |
| Repo map with the project's classes and functions | Aider | automatic |
| Semantic code search (RAG), indexed in the background | Cursor | automatic, `/index` |
| Custom commands in `.mydevagent/commands/*.md` | Claude Code | `/name` |
| Skills in folders (`SKILL.md` + support files), loaded only when needed | Claude Code, BluAgent | `/skill` |
| Plugins in Claude Code format (commands, skills, agents), including those already installed in Claude Code | Claude Code | `/plugin` |
| Hooks: automatic commands before/after tools, on submit and at the end (Claude Code format) | Claude Code | `/hooks` |
| MCP servers (GitHub, databases, browser…), including those already configured in Claude Code | Claude Code | `/mcp` |
| Subagents with separate context (`.claude/agents/*.md`, also from plugins) | Claude Code | `/agents` |
| Ready-made projects: website, Pygame game, Discord bot, FastAPI API, Python program with tests | MyDevAgent | `/new` |
| Learn mode: explains what it does and lets you write a piece of code (`TODO(you)`) | Claude Code (Learning style) | `/learn` |
| Preview of websites on localhost: the agent opens the page, looks at it (screenshot + vision model) and reads console errors | Claude Code + Playwright MCP | `/preview`, automatic |
| Multiple folders at once (e.g. frontend and backend): the agent reads, searches and edits in all of them | Claude Code | `/add-dir`, `--add-dir` |
| Multiplayer: friends on your network follow the session from the browser and message the agent (you confirm the edits) | MyDevAgent | `/multi` |
| Stats: requests, tokens, files and lines changed, tests, activity chart and day streak | Claude Code | `/stats` |
| Conversation compaction | Claude Code | `/compact`, automatic |
| Multi-agent team with review on the real diff and debate on the plan | MyDevAgent | `/balanced` `/deep` `/ultra-deep` |

---

## Install and run in 5 minutes

### Automatic
```bash
git clone -b EN-MyDevAgent https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent mydevagent && cd mydevagent
./scripts/install.sh               # picks the profile based on your GPU; or: ./scripts/install.sh gpu8
./run.sh                           # starts MyDevAgent, without activating the virtual environment
```
On Windows: `powershell -ExecutionPolicy Bypass -File scripts\install.ps1`, then `run.bat`.
No git? Download the [zip](https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent/archive/refs/heads/EN-MyDevAgent.zip).

To use it on one of your projects, launch `run.sh` (or `run.bat`) from the project folder:
`cd ~/code/my-project && ~/mydevagent/run.sh`. If MyDevAgent isn't installed yet, `run.sh` installs it.

### Manual
```bash
# 1. Ollama  →  https://ollama.com/download
ollama pull qwen2.5-coder:7b && ollama pull qwen2.5-coder:1.5b && ollama pull nomic-embed-text

# 2. MyDevAgent (Python 3.10+)
git clone -b EN-MyDevAgent https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent mydevagent && cd mydevagent
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[server,search]"
cp .env.example .env                                      # profile, search keys (optional)

# 3. Check and use
mydevagent doctor
mydevagent
```

### Which profile?
| Profile | Hardware | Main model |
|---|---|---|
| `cpu` | CPU only, 16 GB RAM | qwen2.5-coder:3b |
| `gpu8` | 8 GB GPU (RTX 3060/4060, M1/M2 16 GB) | qwen2.5-coder:7b |
| `gpu16` | 12–16 GB GPU | qwen2.5-coder:14b |
| `gpu24` | 24 GB GPU / Mac 32 GB+ | qwen3-coder:30b (MoE, very fast) |

Change profile with `MYDEVAGENT_PROFILE=gpu16` in `.env` or `mydevagent -p gpu16`. `mydevagent doctor` tells you
which profile fits your hardware.

### If it doesn't start
On startup MyDevAgent checks the model server and the profile's models on its own, and suggests what to do.

| Problem | Solution |
|---|---|
| `permission denied` on `run.sh` or `install.sh` | `bash run.sh` (or `chmod +x run.sh scripts/install.sh`) |
| `mydevagent: command not found` | use `./run.sh`, or activate the environment: `source .venv/bin/activate` (Windows: `run.bat`) |
| "The model server isn't responding" | start Ollama: `ollama serve`, or open the Ollama app on Windows/macOS |
| "The model … isn't installed" (404 error) | on startup choose **Download them now** or **Use the models I already have**; in the UI: `/pull <name>`, `/model <name> --save` |
| "The model is too slow" / "doesn't fit in memory" | smaller profile (`mydevagent -p cpu`) or `/fast`; `mydevagent bench` measures the speed |

`mydevagent doctor` (or `/doctor` in the UI) shows everything at once: backend, models, hardware, network, sandbox.

### Updating
In the UI type `/update` (or `mydevagent update` from the terminal): it fetches what's new with `git pull`, updates
the dependencies only if they changed and lists what's new; then restart MyDevAgent. Models, `.env`,
memory and sessions stay as they are. On startup Vio lets you know when there's something new on GitHub.

If you downloaded the zip instead of cloning, link the folder to GitHub once (`.env` and `.venv`
are kept):

```
cd <MyDevAgent folder>
git init
git remote add origin https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent.git
git fetch origin EN-MyDevAgent
git checkout -f -B EN-MyDevAgent origin/EN-MyDevAgent
```

## Usage

```bash
mydevagent                                        # interactive Claude Code-style interface (see docs/TUI.md)
mydevagent --continue                             # resumes the last session in this folder
mydevagent --permissions auto-edit                # starts with automatic edits (commands need confirmation)
mydevagent bench                                  # measures model speed on your PC
mydevagent ask "Write a thread-safe LRU cache in Go with tests"
mydevagent ask "Why does it crash?" -f app/main.py -f error.log
mydevagent ask "/deep FastAPI API for S3 uploads with JWT auth, Postgres and Docker"
mydevagent ask "Redo this UI in React + Tailwind" -i mockup.png
mydevagent ask "What's the latest version of Next.js and what changed? @web"
cat diff.patch | mydevagent ask - -q               # from stdin, answer only
mydevagent route "..."                            # shows the chosen mode and agents (0 tokens)
mydevagent agents                                 # table of the 35 agents
mydevagent index                                  # indexes the current project for RAG
mydevagent serve                                  # OpenAI-compatible server on :8000
ollama run mydevagent                             # single-agent model (after `ollama create`, see below)
```

In the interface, ask for whatever you want ("add pagination to /users and the tests"): the agent explores the
project, edits the files showing you the diffs (in `ask` mode it asks for confirmation), runs the tests and fixes things.
`/` commands · `@file` attach · `!command` shell · `#note` memory · `Shift+Tab` permissions · `Esc` interrupts ·
`/undo` undoes · `/ultra-deep` for big jobs. Full guide: [docs/TUI.md](docs/TUI.md).

In your message you can steer the team: `/fast`, `/balanced`, `/deep`, `/ultra-deep`, `@security`, `@perf`,
`@web`, `@db`, `@fe`, `@be`, `@devops`, `@review`, `@docs`, `@mobile`, `@cloud`, `@gdpr`…

## Online and offline
- **Offline**: everything works; the Research Agent turns itself off and the team flags what should be
  verified (versions, recent APIs). Force it with `MYDEVAGENT_OFFLINE=1`.
- **Online**: search with the fallback chain **Tavily → Firecrawl → SearXNG → DuckDuckGo**.
  Without keys it works with DuckDuckGo (`pip install ddgs`, included in `[search]`). For the best
  quality set `TAVILY_API_KEY` (free plan) or run a self-hosted SearXNG
  (`docker compose -f deploy/docker-compose.yml up -d searxng` + `SEARXNG_URL=http://localhost:8080`).
  Fetched pages go through an anti-SSRF filter (no access to localhost/private IPs).

## Tools and security
| Tool | Notes |
|---|---|
| `run_code` | **Docker** sandbox: `--network none`, read-only filesystem, nobody user, CPU/RAM/PID limits, timeout. Without Docker the tests are skipped (the `local` backend requires `allow_unsafe_local: true`) |
| `read_file` `list_dir` `grep` | confined to the workspace, refuse `.env`/keys, block path traversal and symlinks |
| `write_file` `git_commit` | **disabled** by default (`tools.filesystem.allow_write`, `tools.git.allow_commit`) |
| `git_status` `git_diff` `git_log` | read-only |
| `web_search` `web_fetch` | online only, anti-SSRF |
| `rag_search` | local index in `.mydevagent/` (embeddings or lexical fallback) |
| **agent**: `edit_file` `write_file` `bash` `run_tests` | in the project folder, with the permissions of the chosen mode; checkpoint before every edit; `rm -rf`, `sudo`, `git push --force`, `curl … \| sh` **always** ask for confirmation; `.env`/keys never read or written |
| vision | `vision` tier (qwen2.5vl) for screenshots, mockups, errors in images |

The server honors `MYDEVAGENT_API_KEY` (Bearer) — required if you expose it beyond localhost.

## Deploy
| Backend | Guide |
|---|---|
| Ollama (recommended) | [deploy/ollama.md](deploy/ollama.md) · single-agent models: `ollama create mydevagent -f modelfiles/Modelfile.gpu8` |
| LM Studio | [deploy/lmstudio.md](deploy/lmstudio.md) |
| llama.cpp (+ speculative decoding) | [deploy/llamacpp.sh](deploy/llamacpp.sh) |
| vLLM (+ prefix caching) | [deploy/vllm.sh](deploy/vllm.sh) |
| Docker Compose (Ollama + SearXNG + server) | [deploy/docker-compose.yml](deploy/docker-compose.yml) |
| Hugging Face | models download from HF with llama.cpp (`-hf`) or vLLM; fine-tuning starts from HF (Unsloth) |

Any OpenAI-compatible server works: just set `LLM_BASE_URL` and the model names in the profile.

## IDE integration
- **MyDevAgent Studio** (Windows): the VSCodium-based editor with Vio built in, chat, edit confirmation,
  Ctrl+I and Tab. [Download the installer](https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent/releases/download/studio-en/MyDevAgent-Studio-Setup-EN.exe),
  sources on the [EN-MyDevAgent-Studio](https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent/tree/EN-MyDevAgent-Studio) branch
  (uses `mydevagent bridge`, see `mydevagent/bridge.py`)
- **VS Code + Continue.dev** (multi-agent chat, inline edit, autocomplete, @codebase):
  [integrations/vscode.md](integrations/vscode.md) + [integrations/continue/config.yaml](integrations/continue/config.yaml)
- **GitHub Copilot Chat** with local models (Ollama): [integrations/vscode.md](integrations/vscode.md#2-github-copilot-chat-with-local-models-byok)
- **Cursor**: [integrations/cursor.md](integrations/cursor.md)
- **Aider, Cline, Open WebUI, OpenAI SDK, use as a library**: [integrations/aider.md](integrations/aider.md)

## Customization, speed, fine-tuning
- [docs/CUSTOMIZATION.md](docs/CUSTOMIZATION.md) — models, agents, teams, custom tools, RAG, QLoRA
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — how to make it faster and more token-efficient
- [finetune/README.md](finetune/README.md) — training on your repositories

## Project structure
```
config/settings.yaml      hardware profiles, modes, tools, server
config/agents.yaml        the 15 core agents (role, tier, budget, sections read, tools, keywords)
config/agents_ultra.yaml  the 20 extended agents of /ultra-deep
prompts/                  system persona + 35 role prompts
mydevagent/               router · LangGraph graph · orchestrator · LLM client · tools · CLI · server
mydevagent/agent/         agent mode: tool loop, permissions, checkpoints, memory, repo map
mydevagent/ultra.py       35-agent /ultra-deep pipeline
mydevagent/tui/           Claude Code-style terminal interface
modelfiles/               Ollama Modelfiles per profile (persona built in)
deploy/                   Ollama, LM Studio, llama.cpp, vLLM, Docker Compose, SearXNG
finetune/                 dataset from your repos, QLoRA with Unsloth, GGUF export → Ollama
integrations/             Continue.dev, VS Code/Copilot, Cursor, Aider/Cline
docs/                     agents, performance, customization
tests/                    tests with a fake LLM (no model required): `pytest`
```

## Development
```bash
pip install -e ".[dev]"
pytest            # 76 tests: registry, router, agent, permissions, checkpoints, ultra-deep, tools, UI, server
ruff check .
MYDEVAGENT_FAKE_LLM=1 mydevagent                 # try the interface without a model
```

MIT license (see [LICENSE](LICENSE)). Models have their own licenses (Qwen: Apache-2.0 for most sizes —
always check the model card).
