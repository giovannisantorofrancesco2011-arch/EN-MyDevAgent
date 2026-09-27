// Vio's chat: connects the panel (media/chat.js) to the bridge, opens the diffs of proposed changes and
// guides the first start (MyDevAgent to install, Ollama not running, models to download).
import { spawn } from "child_process";
import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { Bridge, BridgeError, Install, locate } from "./bridge";

type Msg = { type: string; [key: string]: any };
type Action = { id: string; label: string; primary?: boolean };

interface Approval {
  request: string;
  tool: string;
  path?: string;
  before?: string | null;
  after?: string | null;
  diff: string;
}

export const SCHEME = "mydevagent-proposal";
export const TAB_MODEL = "qwen2.5-coder:1.5b-base";
const WIN = process.platform === "win32";
const IGNORE = "**/{node_modules,.git,.venv,venv,__pycache__,dist,build,out,.mydevagent}/**";
const MAX_SELECTION = 50_000;

const config = () => vscode.workspace.getConfiguration("mydevagent");
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function errorInfo(error: unknown): { message: string; hint: string } {
  if (error instanceof BridgeError) return { message: error.message, hint: error.hint };
  return { message: error instanceof Error ? error.message : String(error), hint: "" };
}

export class Chat implements vscode.WebviewViewProvider, vscode.TextDocumentContentProvider {
  private view?: vscode.WebviewView;
  private ready = false;
  private transcript: { role: string; content: string }[] = [];
  private setupCard?: Msg;
  private approvals = new Map<string, Approval>();
  private proposals = new Map<string, string>(); // uri → text of the before/after documents
  private files: string[] = [];
  private missing: string[] = [];
  private lastPrompt = "";
  private connecting?: Promise<void>;
  private ollama = false;
  private pullListener?: (p: any) => void;
  install?: Install;
  root?: string;
  readonly state = {
    connected: false, busy: false, permission: "ask", team: "auto", learn: false, model: "",
    commands: [] as { name: string; description: string }[], agents: {} as Record<string, string>,
    ctx: null as number | null, // % of the model's context in use (/context)
  };
  private readonly stateEmitter = new vscode.EventEmitter<void>();
  readonly onState = this.stateEmitter.event;

  constructor(private readonly context: vscode.ExtensionContext, readonly bridge: Bridge,
              private readonly log: vscode.OutputChannel) {
    bridge.onNotify(({ method, params }) => this.onNotify(method, params));
    bridge.onExit((tail) => this.onCrash(tail));
  }

  // -------------------------------------------------------------------- panel
  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.ready = false;
    const media = vscode.Uri.joinPath(this.context.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    view.webview.html = this.html(view.webview, media);
    view.webview.onDidReceiveMessage((m: Msg) => this.handle(m));
    view.onDidDispose(() => {
      if (this.view === view) this.view = undefined;
    });
  }

  private html(webview: vscode.Webview, media: vscode.Uri): string {
    const uri = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(media, file));
    const nonce = crypto.randomBytes(16).toString("base64");
    const script = (file: string) => `<script nonce="${nonce}" src="${uri(file)}"></script>`;
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data:; style-src ${webview.cspSource}; font-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="${uri("chat.css")}" rel="stylesheet">
</head>
<body>
<div id="app">
  <header class="top">
    <div id="vio" class="vio" title="Pet me!"></div>
    <div class="who">
      <div class="name">Vio <span id="dot" class="dot off"></span><span id="model" class="model"></span></div>
      <div id="says" class="says"></div>
    </div>
    <div class="actions">
      <button class="icon" data-cmd="stats" title="Statistics (/stats)"><svg viewBox="0 0 16 16"><path fill="currentColor" d="M2 13h12v1.2H2zM3 8h2.2v4H3zm3.9-4h2.2v8H6.9zm3.9 2H13v6h-2.2z"/></svg></button>
      <button class="icon" data-cmd="undo" title="Undo Vio's last changes (/undo)"><svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.5" d="M4.5 6.5h5.5a3.25 3.25 0 0 1 0 6.5H6"/><path fill="currentColor" d="M1.5 6.5 5.5 3v7z"/></svg></button>
      <button class="icon" data-cmd="clear" title="New chat (/clear)"><svg viewBox="0 0 16 16"><path fill="none" stroke="currentColor" stroke-width="1.5" d="M8 3v10M3 8h10"/></svg></button>
    </div>
  </header>
  <main id="log"></main>
  <footer class="composer">
    <div id="context" class="context"></div>
    <div class="box">
      <div id="menu" class="menu hidden"></div>
      <textarea id="input" rows="1" placeholder="Ask Vio… (/ commands, @ files)"></textarea>
      <div class="bar">
        <select id="permission" title="What Vio can do without asking"></select>
        <select id="team" title="How many agents work on the request"></select>
        <span class="spacer"></span>
        <button id="send" class="send" title="Send"></button>
      </div>
    </div>
    <div id="hint" class="hint"></div>
  </footer>
</div>
${script("vio.js")}
${script("markdown.js")}
${script("chat.js")}
</body>
</html>`;
  }

  private post(message: Msg): void {
    if (this.view && this.ready) void this.view.webview.postMessage(message);
  }

  /** The panel has (re)loaded: send it again what it needs to know. */
  private onReady(): void {
    this.ready = true;
    this.post({ type: "state", state: this.state });
    if (this.transcript.length) this.post({ type: "history", messages: this.transcript });
    if (this.setupCard) this.post(this.setupCard);
    this.post({ type: "files", files: this.files });
    this.postContext();
    // ponytail: a turn in progress is not redrawn if the panel reloads (only happens when moving it)
  }

  reveal(focusInput = false): void {
    if (this.view) this.view.show(!focusInput);
    else void vscode.commands.executeCommand("mydevagent.chat.focus");
    if (focusInput) setTimeout(() => this.post({ type: "focus" }), 150);
  }

  say(text: string, expression?: string, seconds?: number): void {
    this.post({ type: "say", text, expression, seconds });
  }

  private setup(kind: "info" | "error" | "none", title = "", text = "", actions: Action[] = [], say?: string): void {
    this.setupCard = kind === "none" ? undefined : { type: "setup", kind, title, text, actions, say };
    this.post(this.setupCard || { type: "setup", kind: "none" });
  }

  private setState(change: Record<string, unknown>): void {
    Object.assign(this.state, change);
    void vscode.commands.executeCommand("setContext", "mydevagent.busy", this.state.busy);
    this.post({ type: "state", state: this.state });
    this.stateEmitter.fire();
  }

  // --------------------------------------------------------------- connection
  connect(): Promise<void> {
    if (!this.connecting) this.connecting = this.doConnect().finally(() => (this.connecting = undefined));
    return this.connecting;
  }

  private async doConnect(): Promise<void> {
    this.bridge.stop();
    this.setState({ connected: false, busy: false });
    const folder = vscode.workspace.workspaceFolders?.[0]; // ponytail: with several folders open, work in the first one
    if (!folder || folder.uri.scheme !== "file") {
      return this.setup("info", "Open a folder", "I work inside your project folder: open one and let's get started.",
        [{ id: "open-folder", label: "Open a folder", primary: true }], "Open a folder and let's get started!");
    }
    if (!vscode.workspace.isTrusted) { // restricted mode: reconnects by itself when you trust the folder (extension.ts)
      return this.setup("info", "This folder is not trusted yet",
        "Studio opened it in Restricted Mode: until you trust it, I don't read, edit or run anything in here.",
        [{ id: "trust-folder", label: "I trust this folder", primary: true }], "Can I work here? Tell me you trust it.");
    }
    this.root = folder.uri.fsPath;
    this.install = locate(config().get("path", ""));
    if (!this.install) {
      return this.setup("error", "I can't find MyDevAgent", WIN
        ? "I can install it for you: I'll download Python, Ollama, MyDevAgent and the models (needs Internet and a few GB of space). If you already have it, tell me where it is."
        : "Install it by following MyDevAgent's README, or tell me which folder it is in.",
      [...(WIN ? [{ id: "install", label: "Install MyDevAgent", primary: true }] : []),
        { id: "choose-folder", label: "I already have it: choose the folder" }], "I'm missing my brain: MyDevAgent!");
    }
    this.setup("info", "Waking up…", "Starting MyDevAgent and checking the models.", [], "Waking up…");
    let hello: any;
    try {
      await this.bridge.start(this.install, this.root, config().get("profile", ""));
      hello = await this.bridge.request("hello", { resume: true, permission: config().get("permissions", "ask") });
      await this.bridge.request("set", { team: config().get("team", "auto") });
    } catch (error) {
      const { message, hint } = errorInfo(error);
      return this.setup("error", message, hint || "Check the log for details.",
        [{ id: "retry", label: "Try again", primary: true }, { id: "log", label: "Show the log" }], "I can't wake up…");
    }
    this.ollama = hello.ollama;
    this.transcript = hello.history;
    this.post({ type: "reset" });
    this.setState({ permission: hello.permission, team: config().get("team", "auto"), learn: hello.learn,
      model: hello.models.main, commands: hello.commands, agents: hello.agents });
    if (this.transcript.length) this.post({ type: "history", messages: this.transcript });
    void this.refreshFiles();
    // which MyDevAgent I'm using: if there are several copies on the PC, you see it right away
    const where = hello.home ? `MyDevAgent in ${hello.home}${hello.installed ? ` · version ${hello.installed}` : ""}` : "";
    if (where) {
      this.log.appendLine(where);
      this.post({ type: "notice", text: `🟣 ${where}` });
    }
    if (await this.checkHealth()) this.askTrust(hello.untrusted);
    void this.refreshUsage();
    void this.checkUpdates();
  }

  /** MyDevAgent updates on GitHub? Offers them with the "Update" button (silent when offline). */
  private async checkUpdates(): Promise<void> {
    try {
      const { available } = await this.bridge.request("updates");
      if (available > 0) {
        this.post({ type: "update", count: available, actions: [{ id: "update", label: "Update", primary: true }] });
      }
    } catch {
      // old MyDevAgent (without the method) or no git: no notice
    }
  }

  private async runUpdate(): Promise<void> {
    if (this.state.busy) return this.showError(new BridgeError("I'm working", "Wait for the request to finish, then update."));
    this.setup("info", "Updating MyDevAgent…", "Downloading what's new from GitHub.", [], "Updating myself…");
    let result: any;
    try {
      result = await this.bridge.request("update");
    } catch (error) {
      const { message, hint } = errorInfo(error);
      return this.setup("error", message, hint || "Update from the terminal with /update.",
        [{ id: "dismiss", label: "Close" }], "I couldn't update myself.");
    }
    if (!result.ok) {
      return this.setup("error", "Update failed", result.message, [{ id: "dismiss", label: "Close" }],
        "I couldn't update myself.");
    }
    this.setup("none");
    const news = (result.changes || []).slice(0, 8).map((c: string) => `• ${c}`).join("\n");
    this.post({ type: "notice", text: result.message + (news ? `\n${news}` : "") });
    if (result.restart) {
      await this.connect(); // starts again with the new code
      this.say("Updated! Now I have the latest features.", "love", 5);
    }
  }

  /** The share of context in use, for the top bar. */
  private async refreshUsage(): Promise<void> {
    try {
      const usage = await this.bridge.request("context");
      this.setState({ ctx: usage.percent });
    } catch {
      this.setState({ ctx: null });
    }
  }

  /** Is Ollama running and are the models there? If something is missing, say so in the chat, with a button to fix it. */
  private async checkHealth(): Promise<boolean> {
    const ok = await this.health();
    this.setState({ connected: ok }); // ready only with the models: without them, chat, Ctrl+I and Tab wait
    return ok;
  }

  private async health(): Promise<boolean> {
    let health: any;
    try {
      health = await this.bridge.request("health", { models: [TAB_MODEL] });
    } catch (error) {
      this.showError(error);
      return false;
    }
    if (health.down.length) {
      this.setup("error", this.ollama ? "Ollama is not responding" : "The model server is not responding", this.ollama
        ? "Ollama is the program that runs the models on your computer: start it and I'll try again."
        : `I can't reach ${health.down.join(", ")}: start the server and try again.`,
      [...(this.ollama ? [{ id: "start-ollama", label: "Start Ollama", primary: true }] : []),
        { id: "retry", label: "Try again", primary: !this.ollama }], "The models are asleep…");
      return false;
    }
    this.missing = [...new Set<string>(health.missing.filter((m: any) => m.tier !== "vision").map((m: any) => m.model))];
    if (this.missing.length) {
      this.setup("info", "Some models are missing", `To work I need **${this.missing.join(", ")}**. ` +
        "I'll download them from Ollama just once: it can take a few minutes.",
      [{ id: "pull", label: "Download the models", primary: true }], "I need my models!");
      return false;
    }
    this.setup("none");
    this.say(`Hi! I'm ready: working in ${path.basename(this.root || "")}.`, "done", 6);
    void this.suggestTabModel(health.has[TAB_MODEL]);
    return true;
  }

  private askTrust(untrusted: { hooks: string[]; mcp: string[] }): void {
    const items = [...untrusted.hooks.map((h) => `hook ${h}`), ...untrusted.mcp.map((m) => `server MCP ${m}`)];
    if (!items.length) return;
    this.setup("info", "This project wants to enable some commands",
      `The project has: ${items.map((i) => `\`${i}\``).join(", ")}. Enable them only if you trust whoever wrote the project.`,
      [{ id: "trust", label: "I trust it, enable them" }, { id: "dismiss", label: "No" }]);
  }

  private async suggestTabModel(installed: boolean): Promise<void> {
    if (!this.ollama || config().get("tab.model", "")) return;
    if (installed) return void config().update("tab.model", TAB_MODEL, vscode.ConfigurationTarget.Global);
    if (this.context.globalState.get("tabModelAsked")) return;
    await this.context.globalState.update("tabModelAsked", true);
    const choice = await vscode.window.showInformationMessage(
      `Tab suggestions work best with the ${TAB_MODEL} model (about 1 GB). Download it?`, "Download", "No thanks");
    if (choice !== "Download") return;
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `Downloading ${TAB_MODEL}` },
      async (progress) => {
        let last = 0;
        this.pullListener = (p) => {
          const pct = p.total ? Math.floor((100 * p.completed) / p.total) : 0;
          if (pct > last) progress.report({ increment: pct - last, message: `${pct}%` });
          last = Math.max(last, pct);
        };
        try {
          await this.bridge.request("pull", { models: [TAB_MODEL] });
          await config().update("tab.model", TAB_MODEL, vscode.ConfigurationTarget.Global);
        } catch (error) {
          void vscode.window.showErrorMessage(`Download failed: ${errorInfo(error).message}`);
        } finally {
          this.pullListener = undefined;
        }
      });
  }

  private onCrash(tail: string): void {
    const busy = this.state.busy;
    this.setState({ connected: false, busy: false });
    if (busy) this.post({ type: "turnEnd", answer: "", cancelled: false, error: { message: "MyDevAgent exited", hint: tail } });
    this.setup("error", "MyDevAgent exited", tail || "Check the log for details.",
      [{ id: "retry", label: "Restart", primary: true }, { id: "log", label: "Show the log" }], "Oops, I fell asleep!");
  }

  // ---------------------------------------------------------- from the panel
  /** A message from the panel (or from an editor command): errors end up in the chat. */
  async handle(m: Msg): Promise<void> {
    try {
      switch (m.type) {
        case "ready": return this.onReady();
        case "send": return await this.send(String(m.text), m.context !== false);
        case "stop": return void (await this.bridge.request("cancel"));
        case "approve": return await this.answer(m.request, m.answer, m.feedback || "");
        case "review": return await this.openProposal(m.request, false);
        case "open": return await this.openFile(m.path);
        case "command": return await this.command(m.name, m.days);
        case "set": return await this.set(m);
        case "action": return await this.action(m.id);
        case "copy": return void (await vscode.env.clipboard.writeText(m.text));
        case "insert": return await this.insert(m.text);
        case "link":
          if (/^https?:\/\//.test(m.href)) await vscode.env.openExternal(vscode.Uri.parse(m.href));
      }
    } catch (error) {
      this.showError(error);
    }
  }

  private showError(error: unknown, actions: Action[] = []): void {
    this.post({ type: "error", ...errorInfo(error), actions });
  }

  async send(text: string, withContext: boolean): Promise<void> {
    if (!this.state.connected) {
      this.showError(new BridgeError("I'm not connected yet", "See the message above to fix it."),
        [{ id: "retry", label: "Try to connect again" }]);
      return;
    }
    if (config().get("saveBeforeSend", true)) await vscode.workspace.saveAll(false);
    this.lastPrompt = text;
    this.post({ type: "user", text });
    this.setState({ busy: true });
    try {
      await this.bridge.request("prompt", { text, context: withContext ? this.editorContext(true) : {} });
      this.transcript.push({ role: "user", content: text });
    } catch (error) {
      this.setState({ busy: false });
      this.post({ type: "turnEnd", answer: "", cancelled: false, error: errorInfo(error) });
    }
  }

  private async set(m: Msg): Promise<void> {
    const change: Record<string, unknown> = {};
    for (const key of ["permission", "team", "learn"]) if (key in m) change[key] = m[key];
    this.setState(change);
    if (this.bridge.running) await this.bridge.request("set", change);
    if ("permission" in change) await config().update("permissions", change.permission, vscode.ConfigurationTarget.Global);
    if ("team" in change) await config().update("team", change.team, vscode.ConfigurationTarget.Global);
  }

  /** The user changed the settings by hand. */
  async onConfig(e: vscode.ConfigurationChangeEvent): Promise<void> {
    if (e.affectsConfiguration("mydevagent.path") || e.affectsConfiguration("mydevagent.profile")) return this.connect();
    const permission = config().get("permissions", "ask");
    const team = config().get("team", "auto");
    if (permission !== this.state.permission || team !== this.state.team) await this.set({ type: "set", permission, team });
  }

  private async command(name: string, days?: number): Promise<void> {
    if (name === "undo") {
      const undone = await this.bridge.request("undo");
      this.post({ type: "notice", text: undone ? `↩ Undid the changes of "${undone.label}": ${undone.files.join(", ")}`
        : "Nothing to undo." });
      if (undone) this.say("Done: I put everything back the way it was.", "done", 4);
    } else if (name === "diff") {
      const { diff } = await this.bridge.request("diff");
      if (!diff.trim()) return this.post({ type: "notice", text: "No changes in this session." });
      await vscode.window.showTextDocument(await vscode.workspace.openTextDocument({ language: "diff", content: diff }));
    } else if (name === "clear") {
      await this.bridge.request("clear");
      this.transcript = [];
      this.post({ type: "reset" });
      void this.refreshUsage();
      this.say("New chat: go ahead!", "done", 4);
    } else if (name === "context") {
      const usage = await this.bridge.request("context");
      this.setState({ ctx: usage.percent });
      this.post({ type: "contextUsage", ...usage });
    } else if (name === "compact") {
      this.setState({ busy: true });
      this.say("Summarizing the conversation…", "think");
      try {
        const result = await this.bridge.request("compact");
        this.setState({ ctx: result.usage.percent });
        this.post({ type: "compacted", ...result });
        if (result.compacted) this.say("Done: I made room in my memory.", "done", 4);
      } finally {
        this.setState({ busy: false });
      }
    } else if (name === "update") {
      await this.runUpdate();
    } else if (name === "stats") {
      const result = await this.bridge.request("stats", days ? { days } : {});
      this.post({ type: "stats", ...result, label: days ? `last ${days} days` : "all time" });
    }
  }

  private async action(id: string): Promise<void> {
    switch (id) {
      case "open-folder": return void (await vscode.commands.executeCommand("workbench.action.files.openFolder"));
      case "install": return this.runInstaller();
      case "choose-folder": {
        const picked = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false,
          title: "Where is the MyDevAgent folder? (the one with .venv)" });
        if (picked) await config().update("path", picked[0].fsPath, vscode.ConfigurationTarget.Global);
        return; // onConfig reconnects
      }
      case "retry": return this.connect();
      case "trust-folder": return void vscode.commands.executeCommand("workbench.trust.manage");
      case "log": return this.log.show();
      case "pull": return this.pullMissing();
      case "start-ollama": return this.startOllama();
      case "trust":
        await this.bridge.request("trust");
        this.setup("none");
        return this.say("OK, I enabled the project's hooks and MCP servers.", "done", 5);
      case "dismiss": return this.setup("none");
      case "update": return this.runUpdate();
      case "retry-last": if (this.lastPrompt) this.post({ type: "fill", text: this.lastPrompt });
    }
  }

  private async pullMissing(): Promise<void> {
    this.setup("info", "Downloading the models…", "You can keep using the editor while I download.", [], "Downloading my models…");
    try {
      await this.bridge.request("pull", { models: this.missing });
    } catch (error) {
      const { message, hint } = errorInfo(error);
      return this.setup("error", "Download failed", `${message}${hint ? `: ${hint}` : ""}`,
        [{ id: "pull", label: "Try again", primary: true }]);
    }
    await this.checkHealth();
  }

  private async startOllama(): Promise<void> {
    const app = WIN ? path.join(process.env.LOCALAPPDATA || "", "Programs", "Ollama", "ollama app.exe") : "";
    const [command, args] = app && fs.existsSync(app) ? [app, []] : ["ollama", ["serve"]];
    const proc = spawn(command, args, { detached: true, stdio: "ignore", windowsHide: true });
    proc.on("error", (error) => this.log.appendLine(`Ollama does not start: ${error.message}`));
    proc.unref();
    this.setup("info", "Starting Ollama…", "Just a moment…", [], "Waking Ollama up…");
    for (let i = 0; i < 10; i++) {
      await sleep(1500);
      const health = await this.bridge.request("health").catch(() => undefined);
      if (health && !health.down.length) break;
    }
    if (await this.checkHealth()) this.say("Ollama is awake: let's get to work!", "done", 5);
  }

  private async runInstaller(): Promise<void> {
    const script = path.join(this.context.extensionPath, "setup", "install-mydevagent.ps1");
    const task = new vscode.Task({ type: "mydevagent" }, vscode.TaskScope.Global, "Install MyDevAgent", "MyDevAgent",
      new vscode.ProcessExecution("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]));
    task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Dedicated, clear: true };
    const execution = await vscode.tasks.executeTask(task);
    this.setup("info", "Installing MyDevAgent…", "Follow the progress in the terminal below: when it finishes I'll connect by myself.",
      [], "Installing my brain…");
    const code = await new Promise<number | undefined>((resolve) => {
      const sub = vscode.tasks.onDidEndTaskProcess((e) => {
        if (e.execution === execution) {
          sub.dispose();
          resolve(e.exitCode);
        }
      });
    });
    if (code === 0) return this.connect();
    this.setup("error", "The installation failed", "The terminal below shows why. Once that is fixed, try again.",
      [{ id: "install", label: "Try again", primary: true }, { id: "choose-folder", label: "Choose the folder" }]);
  }

  // ---------------------------------------------------------- from the bridge
  private onNotify(method: string, params: any): void {
    if (method === "event") this.post({ type: "event", event: params.event });
    else if (method === "chunk") this.post({ type: "chunk", text: params.text });
    else if (method === "approval") void this.onApproval(params);
    else if (method === "turn_end") void this.onTurnEnd(params);
    else if (method === "pull") {
      const done = params.status === "success";
      this.post({ type: "pull", ...params, done });
      this.pullListener?.(params);
    }
  }

  private async onApproval(approval: Approval): Promise<void> {
    this.approvals.set(approval.request, approval);
    void vscode.commands.executeCommand("setContext", "mydevagent.pendingApproval", true);
    if (!this.view?.visible) this.reveal();
    this.post({ type: "approval", approval });
    if (approval.after !== null && approval.after !== undefined && config().get("autoDiff", true)) {
      await this.openProposal(approval.request, true);
    }
  }

  private async onTurnEnd(end: any): Promise<void> {
    this.setState({ busy: false });
    this.post({ type: "turnEnd", answer: end.answer, cancelled: end.cancelled, error: end.error });
    if (end.answer) this.transcript.push({ role: "assistant", content: end.answer });
    for (const request of [...this.approvals.keys()]) await this.closeApproval(request);
    if (end.files?.length) void this.refreshFiles();
    void this.refreshUsage();
  }

  // ------------------------------------------------------ proposed changes
  private proposalUri(approval: Approval, side: "before" | "after"): vscode.Uri {
    const file = (approval.path || "file").replace(/\\/g, "/");
    return vscode.Uri.from({ scheme: SCHEME, path: "/" + file.replace(/^\/+/, ""), query: `${approval.request}-${side}` });
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.proposals.get(uri.toString()) ?? "";
  }

  /** Opens the before/after comparison: the ✓ and ✗ buttons at the top right apply or reject. */
  async openProposal(request: string, preserveFocus: boolean): Promise<void> {
    const approval = this.approvals.get(request);
    if (!approval || approval.after === null || approval.after === undefined) return;
    const before = this.proposalUri(approval, "before");
    const after = this.proposalUri(approval, "after");
    this.proposals.set(before.toString(), approval.before ?? "");
    this.proposals.set(after.toString(), approval.after);
    const name = path.basename(approval.path || "file");
    const title = approval.before === null ? `${name} (new file proposed by Vio)` : `${name} (change proposed by Vio)`;
    await vscode.commands.executeCommand("vscode.diff", before, after, title, { preview: true, preserveFocus });
  }

  /** The ✓/✗ in the diff toolbar (or from the Command Palette): applies to the open proposal or to the latest one. */
  requestFor(uri?: vscode.Uri): string | undefined {
    if (uri?.scheme === SCHEME) return uri.query.replace(/-(before|after)$/, "");
    return [...this.approvals.keys()].pop();
  }

  async answer(request: string | undefined, answer: "yes" | "always" | "no", feedback: string): Promise<void> {
    if (!request || !this.approvals.has(request)) return;
    this.post({ type: "approvalClosed", request, answer, feedback });
    await this.closeApproval(request);
    await this.bridge.request("approval_reply", { request, answer, feedback });
  }

  private async closeApproval(request: string): Promise<void> {
    this.approvals.delete(request);
    void vscode.commands.executeCommand("setContext", "mydevagent.pendingApproval", this.approvals.size > 0);
    const tabs = vscode.window.tabGroups.all.flatMap((group) => group.tabs).filter((tab) =>
      tab.input instanceof vscode.TabInputTextDiff && tab.input.modified.scheme === SCHEME &&
      tab.input.modified.query.startsWith(`${request}-`));
    if (tabs.length) await vscode.window.tabGroups.close(tabs, true);
    for (const key of [...this.proposals.keys()]) if (key.includes(`${request}-`)) this.proposals.delete(key);
  }

  // ------------------------------------------------------------- editor
  relative(file: string): string {
    const rel = this.root ? path.relative(this.root, file) : file;
    return (rel && !rel.startsWith("..") && !path.isAbsolute(rel) ? rel : file).replace(/\\/g, "/");
  }

  /** The open file and the selection: Vio sees them without you having to mention them. */
  private editorContext(withText: boolean): Record<string, unknown> {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.scheme !== "file") return {};
    const file = this.relative(editor.document.uri.fsPath);
    const sel = editor.selection;
    if (sel.isEmpty) return { file, line: sel.active.line + 1 };
    const end = sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1;
    const selection: Record<string, unknown> = { start: sel.start.line + 1, end };
    if (withText) selection.text = editor.document.getText(sel).slice(0, MAX_SELECTION);
    return { file, selection };
  }

  postContext(): void {
    const context = this.editorContext(false);
    this.post({ type: "context", context: context.file ? context : null });
  }

  async refreshFiles(): Promise<void> {
    if (!this.root) return;
    const uris = await vscode.workspace.findFiles(new vscode.RelativePattern(this.root, "**/*"), IGNORE, 5000);
    this.files = uris.map((uri) => this.relative(uri.fsPath)).sort();
    this.post({ type: "files", files: this.files });
  }

  private async openFile(file: string): Promise<void> {
    const full = path.isAbsolute(file) || !this.root ? file : path.join(this.root, file);
    if (!fs.existsSync(full)) return this.post({ type: "notice", text: `Cannot find ${file}` });
    await vscode.window.showTextDocument(vscode.Uri.file(full), { preview: true });
  }

  private async insert(text: string): Promise<void> {
    const editor = vscode.window.activeTextEditor ?? vscode.window.visibleTextEditors[0];
    if (!editor) return this.post({ type: "notice", text: "Open a file to insert the code." });
    await editor.edit((edit) => editor.selections.forEach((sel) => edit.replace(sel, text)));
    await vscode.window.showTextDocument(editor.document, editor.viewColumn);
  }
}
