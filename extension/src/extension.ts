// MyDevAgent inside the editor: Vio's chat, changes to confirm, Ctrl+I and Tab.
import * as vscode from "vscode";
import { Bridge } from "./bridge";
import { Chat, SCHEME } from "./chat";
import { InlineEdit } from "./inline";
import { Tab } from "./tab";

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel("MyDevAgent");
  const bridge = new Bridge(log);
  const chat = new Chat(context, bridge, log);
  const inline = new InlineEdit(chat);

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.command = "mydevagent.focus";
  const tabStatus = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  tabStatus.command = "mydevagent.toggleTab";
  const paint = () => {
    const { connected, busy, model } = chat.state;
    status.text = !connected ? "$(circle-slash) Vio" : busy ? "$(loading~spin) Vio is working" : "$(hubot) Vio";
    status.tooltip = connected ? `Vio is ready (${model}) · Ctrl+L for the chat` : "Vio is not connected: click to see why";
    const on = vscode.workspace.getConfiguration("mydevagent").get("tab.enabled", true);
    tabStatus.text = on ? "$(sparkle) Tab" : "$(circle-slash) Tab";
    tabStatus.tooltip = on ? "Tab suggestions on (click to turn them off)" : "Tab suggestions off (click to turn them on)";
    status.show();
    tabStatus.show();
  };
  paint();

  let contextTimer: NodeJS.Timeout | undefined;
  const contextChanged = () => {
    clearTimeout(contextTimer);
    contextTimer = setTimeout(() => chat.postContext(), 150);
  };
  let filesTimer: NodeJS.Timeout | undefined;
  const filesChanged = () => {
    clearTimeout(filesTimer);
    filesTimer = setTimeout(() => void chat.refreshFiles(), 1500);
  };
  const watcher = vscode.workspace.createFileSystemWatcher("**/*", false, true, false);

  const command = (id: string, run: (...args: any[]) => unknown) => vscode.commands.registerCommand(id, run);
  const chatCommand = (name: string) => {
    chat.reveal();
    return chat.handle({ type: "command", name });
  };
  context.subscriptions.push(
    log, bridge, inline, status, tabStatus, watcher,
    vscode.window.registerWebviewViewProvider("mydevagent.chat", chat, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, chat),
    vscode.languages.registerCodeLensProvider([{ scheme: "file" }, { scheme: "untitled" }], inline),
    vscode.languages.registerInlineCompletionItemProvider({ pattern: "**" }, new Tab(chat)),
    chat.onState(paint),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("mydevagent")) {
        paint();
        void chat.onConfig(e);
      }
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => void chat.connect()),
    vscode.workspace.onDidGrantWorkspaceTrust(() => void chat.connect()),
    vscode.window.onDidChangeActiveTextEditor(contextChanged),
    vscode.window.onDidChangeTextEditorSelection(contextChanged),
    watcher.onDidCreate(filesChanged),
    watcher.onDidDelete(filesChanged),
    command("mydevagent.focus", () => chat.reveal(true)),
    command("mydevagent.inlineEdit", () => inline.run()),
    command("mydevagent.acceptInline", () => inline.accept()),
    command("mydevagent.rejectInline", () => inline.reject()),
    command("mydevagent.approve", (uri?: vscode.Uri) => chat.answer(chat.requestFor(uri), "yes", "")),
    command("mydevagent.reject", async (uri?: vscode.Uri) => {
      const feedback = await vscode.window.showInputBox({ title: "Reject the change",
        prompt: "What should Vio do instead? (optional: press Enter to just reject)" });
      if (feedback !== undefined) await chat.answer(chat.requestFor(uri), "no", feedback);
    }),
    command("mydevagent.stop", () => bridge.request("cancel").catch(() => undefined)),
    command("mydevagent.undo", () => chatCommand("undo")),
    command("mydevagent.stats", () => chatCommand("stats")),
    command("mydevagent.newChat", () => chatCommand("clear")),
    command("mydevagent.toggleTab", () => {
      const config = vscode.workspace.getConfiguration("mydevagent");
      return config.update("tab.enabled", !config.get("tab.enabled", true), vscode.ConfigurationTarget.Global);
    }),
    command("mydevagent.restart", () => chat.connect()),
    command("mydevagent.install", () => chat.handle({ type: "action", id: "install" })),
    command("mydevagent.showLog", () => log.show()),
  );
  void chat.connect();
}

export function deactivate(): void {
  // the bridge is closed with the subscriptions
}
