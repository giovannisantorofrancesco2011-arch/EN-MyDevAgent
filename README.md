# MyDevAgent Studio

The code editor with **Vio** as its main agent. It is based on [VSCodium](https://vscodium.com) (VS Code without
telemetry) and uses [MyDevAgent](https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent): everything runs on
your computer with Ollama, no cloud and no subscriptions.

> This is the **English edition** (branch `EN-MyDevAgent-Studio`). The Italian edition lives on branch
> [`MyDevAgent-Studio`](https://github.com/giovannisantorofrancesco2011-arch/MyDevAgent/tree/MyDevAgent-Studio).
> The two can be installed side by side.

**[⬇ Download MyDevAgent-Studio-Setup-EN.exe](https://github.com/giovannisantorofrancesco2011-arch/EN-MyDevAgent/releases/download/studio-en/MyDevAgent-Studio-Setup-EN.exe)** (Windows 10/11, 64-bit)

![Vio proposes a change: before/after comparison and confirmation](docs/conferma.png)

*The screenshots were taken in the Italian edition: the English edition looks the same, with English text.*

## How to install

1. Download **MyDevAgent-Studio-Setup-EN.exe** and open it.
2. Windows might say "Windows protected your PC", because the installer is not signed: click
   **More info** and then **Run anyway**.
3. Leave "Also install Python, Ollama and MyDevAgent if they are missing" checked. A window opens and downloads
   what is needed; the first time, the models take a few GB.
4. Open MyDevAgent Studio, open your project folder and talk to Vio in the left sidebar. When Studio asks whether
   you trust the folder, answer yes: Vio does not work in untrusted folders (it reminds you in the chat, with a
   button to trust the folder).

If something is missing (Ollama not running, a model not downloaded, MyDevAgent not found) Vio says so in the chat
and gives you a button to fix it.

The installer sets up the English edition of MyDevAgent (branch `EN-MyDevAgent`).

## What it can do

- **Chat with Vio** (Ctrl+L). Vio reads the project, edits files and runs the tests. It sees the open file and the
  selected code; use `@` to mention other files and `/` for MyDevAgent's commands (`/stats`, `/undo`, `/diff`,
  `/plan`, `/learn`, the agent teams…).
- **Every change is confirmed.** Before touching a file Vio opens the before/after comparison in the editor: ✓ to
  apply, ✗ to reject (optionally explaining why). The permissions let you choose how much it can do on its own, and
  `/undo` always takes you back.
- **Ctrl+I** on the selected code, or wherever you want new code: describe what you want and Vio writes it in the
  file. **Ctrl+Enter** to keep it, **Esc** to discard it.
- **Tab**: code suggestions while you type (best with the `qwen2.5-coder:1.5b-base` model, Vio offers to download it).
- **/stats**: how much you have worked with Vio, in this session and all time, with a chart of your active days.
- **MyDevAgent Dark theme**, black with shades of purple, and an English interface.

| Ctrl+I | /stats |
|---|---|
| ![Ctrl+I: the change stays highlighted until you keep it](docs/ctrl-i.png) | ![Statistics with the chart of active days](docs/stats.png) |

## Shortcuts

| Keys | What it does |
|---|---|
| Ctrl+L | Opens Vio's chat |
| Ctrl+I | Edits the selected code (or writes new code) |
| Ctrl+Enter / Esc | Keeps / discards the Ctrl+I edit |
| Tab | Accepts the suggestion |
| Ctrl+Shift+Backspace | Stops Vio while it works |
| Enter / Shift+Enter | Sends the message / new line |

## Settings

In **File → Preferences → Settings**, search for `mydevagent`:

| Setting | What it is for |
|---|---|
| `mydevagent.path` | MyDevAgent's folder, if it is not found automatically |
| `mydevagent.profile` | MyDevAgent profile to use |
| `mydevagent.permissions` | How much Vio can do without asking |
| `mydevagent.team` | Default agent team |
| `mydevagent.tab.enabled` / `tab.model` / `tab.delay` | Tab suggestions |
| `mydevagent.autoDiff` | Opens the before/after comparison automatically |
| `mydevagent.saveBeforeSend` | Saves files before every message |

## Already using VS Code?

The `studio-en` release also contains `mydevagent-en.vsix`, the same extension: in VS Code or VSCodium go to
**Extensions → … → Install from VSIX**. It needs MyDevAgent installed.

## How it is built

```
extension/            the extension (TypeScript): chat, confirmations, Ctrl+I, Tab, theme
  src/bridge.ts       starts `python -m mydevagent.cli bridge` and talks to it in JSON (one line per message)
  src/chat.ts         Vio's panel, before/after comparisons, installation and models
  src/inline.ts       Ctrl+I
  src/tab.ts          Tab suggestions (Ollama /api/generate)
  media/              the chat page, animated Vio, the markdown renderer
  themes/             MyDevAgent Dark
  setup/              install-mydevagent.ps1 (Python, Ollama, MyDevAgent)
studio/               everything that turns VSCodium into MyDevAgent Studio
  build.ps1           downloads VSCodium, changes name and icons, adds the extension, builds the installer
  installer.iss       Inno Setup installer (per user, no administrator rights)
  defaults/           default settings (theme, no VSCodium updates)
  branding/           Vio's icons
tools/make_icons.py   regenerates the icons
```

The `bridge` command lives in MyDevAgent (`mydevagent/bridge.py`): the extension has no copy of the agent of its
own, it uses the installed one.

## Development

```
cd extension
npm ci
npm run compile && npm test
npm run package          # creates mydevagent.vsix
```

Every push to this branch starts the Windows build on GitHub Actions (`.github/workflows/studio.yml`), which
publishes the installer in the `studio-en` release (recreated on every build, never marked as the latest release,
so it does not replace the Italian edition). For a new version, change `version` in `extension/package.json`.

MIT. VSCodium and VS Code are MIT.
