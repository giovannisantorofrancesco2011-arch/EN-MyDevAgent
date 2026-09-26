# MyDevAgent for VS Code

**Vio**, the coding agent of [MyDevAgent](https://github.com/giovannisantorofrancesco2011-arch/MyDevAgent),
inside your editor. Everything runs on your computer with Ollama: no cloud, no subscriptions.

- **Chat** in the sidebar (Ctrl+L): Vio reads the project, edits files and runs the tests. It sees the open file
  and the selected code; use `@` to mention other files and `/` for commands (`/stats`, `/undo`, `/diff`, `/init`…).
- **Changes to confirm**: before touching a file Vio opens the before/after comparison; apply with ✓ or reject with ✗
  (from the chat too), and go back with `/undo`.
- **Ctrl+I** on the selected code (or wherever you want to add some): describe the change and Vio writes it in the file.
  Ctrl+Enter to keep it, Esc to discard it.
- **Tab**: code suggestions while you type (best with the `qwen2.5-coder:1.5b-base` model).
- **MyDevAgent Dark theme**: black with shades of purple.

It needs MyDevAgent installed (with its `.venv` folder): the extension finds it by itself, or set
`mydevagent.path`. On Windows, if it is missing, Vio can install it with one click (Python, Ollama and models included).

It is part of **MyDevAgent Studio**, the VSCodium-based editor with everything ready to go.
