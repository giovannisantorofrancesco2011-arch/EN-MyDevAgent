# MyDevAgent in VS Code

Three ways, from the most complete to the simplest.

## 1. Continue.dev (recommended: multi-agent chat + autocomplete + @codebase)
1. Install the **Continue** extension from the Marketplace.
2. Start the server: `mydevagent serve` (leave it running in a terminal).
3. Download the autocomplete model: `ollama pull qwen2.5-coder:1.5b-base`.
4. Copy [`continue/config.yaml`](continue/config.yaml) to `~/.continue/config.yaml`
   (Windows: `%USERPROFILE%\.continue\config.yaml`).
5. Open the Continue sidebar (`Ctrl/Cmd+L`) → pick **MyDevAgent (15-agent team)**.
   - `Ctrl/Cmd+I` = inline edit (uses *MyDevAgent Fast*).
   - Tab = local autocomplete.
   - In your message: `/deep`, `@security`, `@perf`, `@web` to steer the team.

## 2. GitHub Copilot Chat with local models (BYOK)
Copilot Chat lets you add local models through **Ollama**:
1. `ollama create mydevagent -f modelfiles/Modelfile.gpu8` (`scripts/install.sh` already does this).
2. In Copilot Chat: model picker → **Manage Models…** → **Ollama** → select `mydevagent`.

This way you use the single-agent MyDevAgent persona (fast). For the full 15-agent team use Continue
(or an "OpenAI Compatible" provider pointed at `http://127.0.0.1:8000/v1`, if your version of VS Code
offers it in *Manage Models*). Note: some Copilot features (e.g. inline completions) may still
require a Copilot account.

## 3. Cline / Roo Code (agents that edit files and run commands)
**OpenAI Compatible** provider → Base URL `http://127.0.0.1:8000/v1`, any API key (or
`MYDEVAGENT_API_KEY`), Model ID `mydevagent-fast` (Cline sends very long prompts with its own instructions:
fast mode avoids multiplying them by 15 agents). Alternatively pick the
**Ollama** provider directly with the `mydevagent` model.
