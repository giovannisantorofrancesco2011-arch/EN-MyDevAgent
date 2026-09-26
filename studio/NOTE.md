**MyDevAgent Studio (English edition)** is the code editor with Vio: based on VSCodium, with MyDevAgent as its main agent. It can be installed next to the Italian edition.

### How to install
1. Download **MyDevAgent-Studio-Setup-EN.exe** below and open it.
2. Windows might say "Windows protected your PC" (the installer is not signed): click **More info** and then **Run anyway**.
3. Leave "Also install Python, Ollama and MyDevAgent if they are missing" checked: a window opens and downloads what is needed (the first time, the models take a few GB).
4. Open MyDevAgent Studio, open your project folder and talk to Vio in the left sidebar. When Studio asks whether you trust the folder, answer yes: Vio does not work in untrusted folders.

### What's inside
- Chat with Vio: it reads the project, edits files and runs the tests; you confirm every change by looking at the before/after comparison.
- **Ctrl+I** on the selected code (or wherever you want new code): Vio writes it the way you ask; **Ctrl+Enter** to keep it, **Esc** to undo.
- **Tab**: code suggestions while you type.
- `/stats`, `/undo`, `/diff`, the agent teams and all of MyDevAgent's commands.
- Black and purple theme, English interface.

`mydevagent-en.vsix` is the same extension for people who already use VS Code or VSCodium.
