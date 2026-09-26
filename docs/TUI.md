# Terminal interface (Claude Code style)

```bash
mydevagent                               # opens the interface in the project folder
mydevagent -p gpu16                      # with another profile
mydevagent --continue                    # resumes the last session in this folder
mydevagent --permissions auto-edit       # automatic edits, commands need confirmation
mydevagent chat --plain                  # old plain chat (pipes, limited terminals)
```

```
> add sub(a, b) in calc.py and a test
⏺ fast · agent · Languages & Frameworks
⏺ Read(calc.py)
  ⎿  calc.py (2 lines)
⏺ Update(calc.py)
@@ -1,2 +1,5 @@
 def add(a, b):
     return a + b
+
+def sub(a, b):
+    return a - b
Apply the edit to calc.py?  1 Yes  2 Yes, and don't ask again  3 No
  › 1
⏺ Test(python -m pytest -q)
  ⎿  ✓ tests passed
I added sub in calc.py and the test test_sub; 2 tests passed.
---
📝 Files changed: calc.py, test_calc.py  (/undo to undo)
✅ Tests: passed (python -m pytest -q)

 agent ⏵ ask (shift+tab) · qwen2.5-coder:7b · mode auto · ● online · ~3.2k tok ·  main
```

## Agent mode (default) and chat mode
- **agent** (`/agent`): the team works **directly on the files** of the folder you opened
  MyDevAgent in, using tools: `read_file`, `list_files`, `grep`, `edit_file` (exact replacement of a
  piece of text), `write_file`, `bash`, `run_tests`, `todo_write`, `web_search`,
  `web_fetch` (reads a web page: the first time for each site it asks for permission, even in `plan`),
  `preview` (looks at a project page on localhost, see "Website preview").
- **chat** (`/chat`): answers with code without touching the files; `/apply` saves it after the diff.

## Permissions (like Claude Code) — `Shift+Tab` to change them
| Mode | Reads | File edits | Commands |
|---|---|---|---|
| `ask` (default) | ✓ | asks for confirmation with the diff | asks for confirmation (read-only commands don't) |
| `auto-edit` | ✓ | automatic | asks for confirmation |
| `plan` | ✓ | ✗ — the agent proposes a plan | read-only only (`ls`, `git status`, …) |
| `auto` | ✓ | automatic | automatic |

- When confirming: **1 Yes** · **2 Yes, and don't ask again** (saves a rule in `.mydevagent/settings.json`,
  e.g. `bash:pytest*` or `edit:src/app.py`) · **3 No**, and you can type what to do instead: the agent receives it.
- `rm -rf`, `sudo`, `git push --force`, `git reset --hard`, `curl … | sh` & co. **always** ask for
  confirmation, even in `auto`. `.env` and keys are never read or modified.
- Before every edit the file is saved in `.mydevagent/checkpoints/` → `/undo` and `/rewind`.
  `.mydevagent/` contains its own `.gitignore`: it doesn't end up in your commits.

## Input
| Key / syntax | Effect |
|---|---|
| `Enter` | send |
| `Alt+Enter` or `Ctrl+J` | new line |
| `Tab` | completes `/commands`, `@files`, `@agents` |
| `↑` / `↓` | history (`~/.mydevagent/history`) |
| `Shift+Tab` | switches permission mode (ask → auto-edit → plan) |
| `Esc` or `Ctrl+C` | while working: interrupts (twice = force). At the prompt `Ctrl+C` clears, twice exits |
| `Esc Esc` | at the prompt: `/rewind` |
| `Ctrl+D` | exits (the session is saved) |
| `@path/file` | attaches the file to the message |
| `@security` `@perf` `@web` `@mobile` `@gdpr` … | brings in an agent |
| `!command` | runs a shell command (e.g. `!pytest -q`) and attaches its output to the next message |
| `#text` | adds a note to `MYDEVAGENT.md` (project memory) |

## Commands
| Command | What it does |
|---|---|
| `/help` | commands and shortcuts |
| `/fast` `/balanced` `/deep` `/ultra-deep` `/auto` | team mode (or `/deep <request>` for a single message) |
| `/plan` | plan mode (read-only) · `/plan <request>` |
| `/permissions [mode]` | shows/changes permissions and saved rules |
| `/undo` · `/rewind` | undoes the last turn · goes back to before a chosen turn |
| `/diff` | all the file changes made in this session |
| `/agent` · `/chat` | work on files · just answer |
| `/apply` | (chat) writes the files from the last answer after the diff |
| `/add-dir [folder]` · `/add-dir remove <folder>` | also works on other folders (e.g. frontend and backend) |
| `/preview [file \| url]` | opens the project's website in the browser, served on localhost |
| `/learn` · `/learn off` | learn mode: explains what it does and lets you write a piece of code |
| `/new [template] [name]` | creates a ready-made project (website, game, bot-discord, api, python) and works inside it |
| `/multi` · `/multi stop` | multiplayer: friends on your network follow the session from the browser and message the agent |
| `/init` | the agent analyzes the project and creates `MYDEVAGENT.md` (commands, architecture, conventions) |
| `/memory [text]` | shows the project memory · adds a note |
| `/compact` | summarizes the conversation (automatic after 10 turns) |
| `/model <name>` · `/models` | changes the model for the session (`--save` remembers it in `.env`) · installed and in-use models |
| `/pull <name>` | downloads a model from Ollama with a progress bar |
| `/skill` · `/skill <name> <request>` | available skills · use a skill for this request |
| `/plugin` · `/plugin install <user/repo>` · `update` · `remove` | plugins in Claude Code format |
| `/hooks` · `/hooks trust` | active hooks · enables the project's ones |
| `/mcp` · `/mcp reload` · `/mcp trust` | MCP servers and their status · restarts them · enables the project's ones |
| `/vio` | say hi to (and pet) Vio, the mascot |
| `/agents` | the 35 agents (core and ultra) |
| `/files` · `/cost` · `/think` | attachments · tokens and time · shows the reasoning |
| `/stats` · `/stats 7` · `/stats 30` · `/stats all` | stats: this session and all time (or the last few days), activity chart, day streak |
| `/index` | indexes the project for semantic search (it usually does this on its own in the background) |
| `/doctor` | checks backend, models, network, sandbox |
| `/update` | updates MyDevAgent (`git pull`, dependencies if changed); on startup Vio lets you know about new versions |
| `/theme [dark\|light]` | theme for diffs and code |
| `/resume` · `/export` · `/clear` · `/exit` | sessions, Markdown export, new conversation, exit |

## Ready-made projects: `/new`
`/new` lists the templates, `/new <template> [name]` creates the project and from then on you work inside it:

| Template | What you get |
|---|---|
| `website` | website with HTML, CSS and JavaScript (light/dark theme), nothing to install |
| `game` | 2D game with Pygame: catch the stars |
| `bot-discord` | Discord bot with `!hello` and `!dice`; the token steps are in its `MYDEVAGENT.md` |
| `api` | API with FastAPI and tests (`python -m pytest -q`) |
| `python` | Python program with tests, to get started |

The project is created in the open folder if it's empty, otherwise in a new subfolder (never inside the
MyDevAgent folder: there it goes next to it). Every template already has its own `MYDEVAGENT.md` with the commands to
run and test it, a `.gitignore` and `git init`. Then just say what you want to change: "make the website
from my drawings", "add enemies to the game".

## Multiple folders at once: `/add-dir`
When a project lives in several folders (the website in `frontend`, the server in `backend`), open MyDevAgent in
one and add the others, like in Claude Code:

```
/add-dir ../backend          adds a folder (remembered for this project)
/add-dir                     shows the folders
/add-dir remove ../backend   removes it
mydevagent --add-dir ../backend   just for this time
```

The agent sees the file list and memory (`MYDEVAGENT.md`) of each folder, and uses them with paths like
`../backend/app.py`: it reads, searches (`grep` looks in all of them), edits with the usual permissions, and `/undo`
works there too. You can attach their files with `@../backend/app.py`. Commands run from the main folder.

## Website preview
When the agent changes a web page it looks at it by itself, with the **Preview** tool: it opens it on
localhost in a headless browser (Chrome, Edge or Chromium, whichever you already have), takes a screenshot that
the `vision` model describes, and reads console errors, missing files and the visible text.
- an HTML file of the project (`index.html` unless told otherwise) is served on `127.0.0.1`, without hidden
  files (`.git`, `.env`) or keys;
- a website with its own server (`npm run dev`, `uvicorn`, Flask…) is opened at its URL: the agent can
  start it with its command, which asks for permission like any command and stays on as long as MyDevAgent
  is open (the output goes to `.mydevagent/preview-server.log`).

The screenshot stays in `.mydevagent/preview.png`. Without a vision model the agent only uses text and errors:
`/pull qwen2.5vl:7b` (or the one in your profile, see `/models`). To see the website yourself: `/preview` opens
`index.html` in the browser, `/preview page.html` another page, `/preview localhost:5173` a server
that's already running. A different browser: `MYDEVAGENT_BROWSER=<path>` in `.env`.

## Learn mode: `/learn`
To learn while you code, like Claude Code's "Learning" style. With `/learn` the agent:
- says in one or two sentences what it's about to do and why;
- writes almost everything, but leaves you **a small piece** (a condition, a loop, the body of a function):
  you'll find it in the code with a `TODO(you):` comment and a hint, not the solution;
- ends with "💡 Good to know": two or three concepts explained in simple words.

When you've written your piece, tell it ("done"): it reads it and tells you what works and what to fix. Tests that
check your piece may fail until you write it, and the review doesn't count that as an error. Vio shows
"learn" above the input; the choice sticks across restarts, `/learn off` turns it off.

## Multiplayer: `/multi`
To code together with a friend on your same network (the same Wi-Fi). `/multi` shows a link
with a code, for example `http://192.168.1.23:8765/?code=K7M2QP`: your friend opens it in the browser (PC or
phone, nothing to install), types their name and joins.
- they see what you see in the terminal: the tools' `⏺`/`⎿` lines, the colored diffs and the answers;
- they message the agent from the page: the message shows up on your side as `› Mark: …` and the agent runs it like
  yours (if you were typing something it stays there, you'll find it afterwards);
- file edits and commands requested by a friend are **always confirmed by you**, even if you're in
  auto mode; they see "waiting for confirmation…" and then how it went. `/`, `!` and `#` commands stay yours only.

Vio tells you when someone connects, and `/multi` shows the link and who has joined again. `/multi stop` (or
quitting MyDevAgent) closes the session. Only give the code to people you trust: through the agent they can read
the project's files (not `.env` and keys). The first time, Windows may ask for a firewall permission for Python:
allow it on private networks.

## Stats: `/stats`
Two columns, **this session** and **all time** (`/stats 7` or `/stats 30`: only the last few days):
requests, tokens, agent working time, tools used, files changed, lines added and removed,
tests passed and failed. Below there's the activity chart like on GitHub (one column per week,
the brighter the purple the more requests you made that day), your day streak 🔥 with your record,
the record day, your favorite hour, the model and team you use most and the projects you work on.
Every request adds a line to `~/.mydevagent/stats.jsonl` (including those made from MyDevAgent Studio):
it stays on your PC, and to start from scratch just delete that file.

## Project memory: `MYDEVAGENT.md`
A file in the project root with commands, architecture and conventions: the agent reads it on every
request (it also reads `AGENTS.md` and `CLAUDE.md` if present, plus `~/.mydevagent/MYDEVAGENT.md` for your
global preferences). Create it with `/init`, add notes on the fly with `#always use pnpm`. If it contains a
line `- test: <command>`, the agent uses that command for tests.

## Custom commands
Create `.mydevagent/commands/<name>.md` (in the project) or `~/.mydevagent/commands/<name>.md` (for all
projects); Claude Code's ones in `.claude/commands/` and `~/.claude/commands/` work too.
`$ARGUMENTS` is replaced with the text after the command, `$1`, `$2`… with the individual words; the
`description:` (in the frontmatter or on the first line) shows up in completion.

```markdown
description: writes the missing tests for a file
Read $ARGUMENTS, find the uncovered cases and write tests with the project's framework. Then run them.
```
→ `/test-file src/api/users.py`

## Skills
Reusable expert instructions, like Claude Code's and BluAgent's skills. The agent only receives
the name + description list: it reads the content (with the `skill` tool, without asking your permission) only
when a request matches, so it doesn't fill up the context.

A skill is a folder with `SKILL.md` and, if needed, support files (templates, examples, scripts) that
the agent can read; or a single `<name>.md` file.

```markdown
---
name: changelog
description: Writes the changelog from git log. Use it when I ask for a changelog or release notes.
---
Read `git log` since the last tag, group by Added / Fixed and follow templates/base.md.
```

Folders read (for the same name, the first one wins): the project's `.mydevagent/skills/` and `.claude/skills/`,
`~/.mydevagent/skills/`, `~/.claude/skills/` (so also the skills you use with Claude Code), plus those
in `MYDEVAGENT_SKILLS_DIRS` in `.env`, separated by `;` on Windows, for example BluAgent's skills:
`MYDEVAGENT_SKILLS_DIRS=C:\Users\Santoro\BluAgent\skills`.

`/skill` lists them, `/skill changelog prepare 1.1` forces the agent to use that skill.

## Plugins (compatible with Claude Code)
A plugin is a folder with `.claude-plugin/plugin.json` and inside it `commands/` (`/name` commands), `skills/`
(skills) and `agents/` (subagents, see below).
It's the same format as Claude Code, so ready-made plugins work:

```
/plugin install anthropics/claude-code     # the official marketplace: 13 plugins
/plugin install user/repo                  # any GitHub repository (or a git URL, or a folder)
/plugin                                    # list with each one's commands, skills and agents
/plugin update claude-code                 # git pull
/plugin remove claude-code                 # removes the downloaded folder (and the plugins it contains)
```

Then the commands show up with `/` (also as `/plugin:command`), skills with `/skill` and subagents with
`/agents`. These are loaded:
plugins in the project's `.mydevagent/plugins/`, those installed with `/plugin install`
(`~/.mydevagent/plugins/`), **those you've already installed in Claude Code** and the folders in
`MYDEVAGENT_PLUGINS_DIRS`.

Plugins' hooks and MCP servers work too (see below). Difference from Claude Code: `!`command``
in commands isn't run before sending: the agent runs it with its tools, asking for permission
as always.

## Hooks (automatic commands)
Commands that run by themselves at certain moments, written like in Claude Code. Example: format every file
the agent edits and forbid it from running `git push`. In `.mydevagent/settings.json` (or `.claude/settings.json`):

```json
{"hooks": {
  "PostToolUse": [{"matcher": "Edit|Write",
                   "hooks": [{"type": "command", "command": "ruff format ."}]}],
  "PreToolUse":  [{"matcher": "Bash",
                   "hooks": [{"type": "command", "command": "python .mydevagent/no_push.py"}]}]
}}
```

| Event | When | What it can do |
|---|---|---|
| `PreToolUse` | before a tool | block it (exit code 2: the text on stderr reaches the agent) |
| `PostToolUse` | after a tool | give the agent a message (exit 2 or `{"decision": "block", "reason": …}`) |
| `UserPromptSubmit` | when you send a request | block it, or add context (whatever it prints) |
| `Stop` · `SubagentStop` | when the agent (or a subagent) wants to finish | ask it to keep going (exit 2 with the reason) |
| `SessionStart` | at startup (in the background) | add context for the whole session |

The command receives the event's JSON on stdin (`tool_name`, `tool_input` with `file_path`, `prompt`…), with
Claude Code's tool names (`Bash`, `Edit`, `Write`, `Read`…); MyDevAgent's names (`bash`, `edit_file`…) also work
in the matcher. `$CLAUDE_PROJECT_DIR` and `${CLAUDE_PLUGIN_ROOT}` are available; on Windows
hooks run with Git's bash, if installed, like in Claude Code.

Where from: `~/.claude/settings.json` (the hooks you already use with Claude Code), `~/.mydevagent/settings.json`, the
plugins, and the project's ones. **The project's ones only run after you say yes**, which MyDevAgent asks
at startup (and again if they change): a downloaded repository must not run commands on your PC on its own.
`/hooks` lists them, `/hooks trust` enables the project's ones, `"disableAllHooks": true` turns them all off.
`prompt`, `async`/`asyncRewake` hooks and those with `if` aren't run yet (they're grayed out in `/hooks`).

## MCP servers (external tools)
With MCP the agent uses tools from other programs: GitHub, databases, browsers, file systems, documentation…
They're configured like in Claude Code, in `.mcp.json` in the project or in `~/.mydevagent/mcp.json` for all
projects:

```json
{"mcpServers": {
  "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
             "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}},
  "docs":   {"type": "http", "url": "https://example.com/mcp",
             "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}}
}}
```

The servers you already use with Claude Code are read too (`~/.claude.json`, including per-project ones)
as well as the plugins' ones. `${VAR}` and `${VAR:-default}` take their values from environment
variables (or from `.env`), so tokens don't end up in the file. As with hooks, servers written in the
project only start after you say yes.

At startup MyDevAgent connects them in the background. The agent receives the list of servers with the tool
names and a single tool, `mcp`: first it asks for a server's arguments, then it uses the tool. This way even
a server with 50 tools doesn't fill up a local model's context. Every use asks for confirmation like a
command (with "Yes, and don't ask again" for that tool); in plan mode only the tools the server
declares read-only are allowed. In hooks the tool is called `mcp__server__name`,
like in Claude Code.

`/mcp` shows the servers and their status, `/mcp reload` restarts them, `/mcp trust` enables the project's ones.
Transports: `stdio` (a command) and `http`. The old `sse` and OAuth login aren't there yet: for remote
servers put the token in `headers`.

## Subagents
Specialized agents that the main agent calls by itself, like in Claude Code: each one works in its own
context (it doesn't see the conversation), with its own tools, and returns only the final report.
This way code search or a review don't fill up the main agent's context, which is small with local
models. A Markdown file in `.mydevagent/agents/` or `.claude/agents/` (project),
`~/.mydevagent/agents/` or `~/.claude/agents/` (all projects), or in plugins' `agents/`:

```markdown
---
name: code-reviewer
description: Reviews code that was just modified. Use it after every significant change.
tools: Read, Grep, Glob
model: haiku
---
You are a strict reviewer: look for bugs, edge cases and unclear names. Don't modify files.
```

`tools` is optional (without it, it has all tools) and accepts Claude Code's names (`Read`, `Grep`, `Glob`,
`Bash`, `Edit`, `Write`…) or MyDevAgent's; `model: haiku` uses the fast model. A subagent's edits
go through the usual permissions and end up in the summary, the review and `/undo`. A
subagent can't call other subagents. When it finishes, the `SubagentStop` hooks run. `/agents` lists them
below the team's agents.

## `/ultra-deep`
35 agents for big jobs: web research if needed, requirements, plan with devil's advocate,
test strategy, implementation, real tests, 10 quality gates and 15 lens reviews in parallel,
Lead Integrator, up to 3 fix rounds, documentation and release. The phases show up as
`✻ phase 4/8 · implementation`. That's 35–50+ model calls: with a local 7B it takes several minutes.

## How it's built (`mydevagent/tui/`)
| File | Role |
|---|---|
| `app.py` | `TuiApp`: input, commands, permissions, `!shell`, attachments; runs `AgentRunner` (or `Orchestrator`) in a thread and draws the events from the queue; confirmations go through the UI thread |
| `render.py` | `TurnRenderer`: `⏺`/`⎿` lines, colored diffs, todos, phases, spinner, streaming Markdown |
| `keys.py` | `Esc` while working (when prompt_toolkit isn't reading the keyboard) |
| `extras.py` | custom commands, compaction, notifications, model warmup |
| `completion.py` · `apply.py` · `session.py` | completion · `/apply` · sessions and `/resume` |
| `multi.py` · `multi.html` | `/multi`: the room (HTTP server with the feed as Server-Sent Events) and the page for friends |
| `statsview.py` | `/stats`: table, activity chart and day streak (the data is collected by `mydevagent/stats.py`) |

### Adding a command
Add the name and description to `COMMANDS` in `app.py` and handle it in `TuiApp.handle_command`
(or, with no code, create a custom command as above).

## Startup check
As soon as it opens, the UI checks the model server and the profile's models in under 2 seconds. If everything is
fine it shows nothing. Otherwise:
- **server off** → explains how to start it (`ollama serve`, the Ollama app or LM Studio); Enter to retry;
- **missing models** → a list with approximate sizes and three choices: **1** download them now (progress
  bar), **2** use the best-fitting models already installed (with the option to remember the choice in `.env`),
  **3** continue;
- **profile not suited to the hardware** → a suggestion, shown only once (e.g. "You have a 12 GB GPU: I
  recommend gpu16").

Errors during use are also explained in plain English with the fix (model not installed, server
unreachable, model too slow, not enough memory), in the UI, in `mydevagent ask` and in the server.

## Vio, the mascot
Vio is MyDevAgent's little purple octopus (lots of tentacles, like its agents), drawn in pixel art right
in the terminal: 14 columns by 4 rows, above the bar where you type, with a line of text next to it.

- Changes expression with the mode: curious in `ask`, excited in `auto-edit`, wearing glasses in `plan`,
  with starry eyes in `auto`, and it has one for chat and for each team (`/fast`, `/balanced`, `/deep`,
  `/ultra-deep`). With `Shift+Tab` it changes right away, together with the line explaining what it will do.
- Wiggles its tentacles and blinks every now and then.
- While the team works it stays below the work in progress and looks around; when the work is done it smiles and tells you how long
  it took, and if something goes wrong its eyes turn into X's.
- `/vio` says hi to it (and pets it).

Preview of all the expressions: `python -m mydevagent.tui.mascot`.
