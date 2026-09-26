// Tab: while you type, Vio suggests what comes next in grey (fill-in-the-middle with the fast model).
import * as vscode from "vscode";
import { type Chat, SCHEME } from "./chat";

const MAX_PREFIX = 4000;
const MAX_SUFFIX = 1500;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class Tab implements vscode.InlineCompletionItemProvider {
  private inflight?: Promise<unknown>;
  private last?: { uri: string; line: number; head: string; text: string };

  constructor(private readonly chat: Chat) {}

  async provideInlineCompletionItems(doc: vscode.TextDocument, pos: vscode.Position, context: vscode.InlineCompletionContext,
                                     token: vscode.CancellationToken): Promise<vscode.InlineCompletionItem[] | undefined> {
    const config = vscode.workspace.getConfiguration("mydevagent");
    if (!config.get("tab.enabled", true) || !this.chat.state.connected || doc.uri.scheme === SCHEME) return;
    const line = doc.lineAt(pos.line).text;
    const head = line.slice(0, pos.character);
    const rest = line.slice(pos.character);
    if (/^\w/.test(rest)) return; // in the middle of a word
    const item = (text: string) => [new vscode.InlineCompletionItem(text, new vscode.Range(pos, pos))];

    // you are typing exactly what I suggested: the rest is already there
    const last = this.last;
    if (last && last.uri === doc.uri.toString() && last.line === pos.line && head.startsWith(last.head)) {
      const typed = head.slice(last.head.length);
      if (last.text.startsWith(typed) && last.text.length > typed.length) return item(last.text.slice(typed.length));
    }

    if (context.triggerKind === vscode.InlineCompletionTriggerKind.Automatic) await sleep(config.get("tab.delay", 250));
    // one at a time: Ollama queues them anyway, and the old ones are no longer needed
    while (this.inflight) {
      await this.inflight.catch(() => undefined);
      if (token.isCancellationRequested) return;
    }
    if (token.isCancellationRequested) return;

    const offset = doc.offsetAt(pos);
    const prefix = doc.getText(new vscode.Range(doc.positionAt(Math.max(0, offset - MAX_PREFIX)), pos));
    const suffix = doc.getText(new vscode.Range(pos, doc.positionAt(offset + MAX_SUFFIX)));
    const multiline = !rest.trim() && (!head.trim() || /[:{([]\s*$/.test(head));
    const request = this.chat.bridge.request<{ text: string }>("complete", {
      prefix, suffix, model: config.get("tab.model", "") || undefined, max_tokens: multiline ? 128 : 48, multiline });
    this.inflight = request;
    let text = "";
    try {
      text = (await request).text;
    } catch {
      return;
    } finally {
      if (this.inflight === request) this.inflight = undefined;
    }
    if (token.isCancellationRequested || !text.trim()) return;
    this.last = { uri: doc.uri.toString(), line: pos.line, head, text };
    return item(text);
  }
}
