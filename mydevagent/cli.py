"""CLI: mydevagent (interactive UI) | chat | ask | serve | bridge | index | doctor | agents | route."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

app = typer.Typer(add_completion=False,
                  help="MyDevAgent — local-first coding assistant with 15 agents. "
                       "With no command it opens the interactive interface.")
console = Console()
err = Console(stderr=True)


def _orchestrator(profile: str | None = None):
    from .config import load_settings
    from .orchestrator import Orchestrator

    overrides = {"profile": profile} if profile else None
    return Orchestrator(load_settings(overrides=overrides))


def _event_printer(target: Console, quiet: bool = False):
    def on_event(event: dict[str, Any]) -> None:
        if quiet:
            return
        kind = event["type"]
        if kind == "route":
            target.print(f"[dim]⟢ {event['mode']} · {' → '.join(event['agents'])}[/dim]")
        elif kind == "agent_start" and event["agent"] not in ("final",):
            target.print(f"[dim]  ▸ {event['name']}…[/dim]")
        elif kind == "agent_end" and event["agent"] != "final":
            tokens = event.get("prompt_tokens", 0) + event.get("completion_tokens", 0)
            mark = "[red]✗[/red]" if event.get("error") else "[green]✓[/green]"
            extra = f" [red]{event['error']}[/red]" if event.get("error") else ""
            target.print(f"[dim]  {mark} {event['name']} {event['ms'] / 1000:.1f}s · {tokens} tok{extra}[/dim]")
        elif kind == "info":
            target.print(f"[dim]  · {event['text']}[/dim]")
        elif kind == "done":
            target.print(f"\n[dim]{event['summary']}[/dim]")

    return on_event


def _read_files(paths: list[Path] | None) -> dict[str, str]:
    files = {}
    for path in paths or []:
        files[str(path)] = path.read_text(encoding="utf-8", errors="replace")
    return files


def _images(paths: list[Path] | None) -> list[str]:
    from .tools.vision import image_to_data_url

    return [image_to_data_url(p) for p in paths or []]


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    profile: str = typer.Option(None, "--profile", "-p", help="cpu | gpu8 | gpu16 | gpu24"),
    continue_last: bool = typer.Option(False, "--continue", "-c", help="Resume the last session in this folder"),
    permissions: str = typer.Option("ask", "--permissions", help="ask | auto-edit | plan | auto"),
    add_dir: list[Path] = typer.Option(None, "--add-dir", help="Another folder to work on (repeatable)"),
) -> None:
    """With no subcommand it opens the Claude Code-style interactive interface."""
    if ctx.invoked_subcommand is None:
        from .tui import run_tui

        run_tui(profile, continue_last=continue_last, permission_mode=permissions, add_dirs=add_dir)


@app.command()
def ask(
    request: str = typer.Argument(..., help="The request ('-' to read from stdin)"),
    file: list[Path] = typer.Option(None, "--file", "-f", exists=True, help="File to attach (repeatable)"),
    image: list[Path] = typer.Option(None, "--image", "-i", exists=True, help="Screenshot/mockup (repeatable)"),
    mode: str = typer.Option("auto", "--mode", "-m", help="auto | fast | balanced | deep"),
    profile: str = typer.Option(None, "--profile", "-p", help="cpu | gpu8 | gpu16 | gpu24"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only the answer, without agent progress"),
    think: bool = typer.Option(False, "--show-thinking", help="Show the model's <think> blocks"),
) -> None:
    """A single request, answer streamed to stdout."""
    if request == "-":
        request = sys.stdin.read()
    orch = _orchestrator(profile)
    on_event = _event_printer(err, quiet)
    try:
        for chunk in orch.run(request, files=_read_files(file), images=_images(image), mode=mode,
                              on_event=on_event, show_thinking=think):
            sys.stdout.write(chunk)
            sys.stdout.flush()
    except Exception as exc:
        _print_error(err, exc, orch.settings)
        raise typer.Exit(1) from None
    sys.stdout.write("\n")


def _print_error(target: Console, exc: Exception, settings) -> None:
    from .health import explain_error

    title, hint = explain_error(exc, settings)
    target.print(f"\n[red]✗ {escape(title)}[/red]\n[dim]→ {escape(hint)}[/dim]")


CHAT_HELP = """[bold]Commands[/bold]: /fast /balanced /deep /auto (mode) · /file <path> (attach) · /image <path>
/files (list attachments) · /clear-files · /think (show reasoning) · /reset (new conversation)
/agents · /exit   —   in the text: @security @perf @web @db ... to involve specific agents"""


@app.command()
def chat(
    profile: str = typer.Option(None, "--profile", "-p", help="cpu | gpu8 | gpu16 | gpu24"),
    mode: str = typer.Option("auto", "--mode", "-m"),
    plain: bool = typer.Option(False, "--plain", help="Plain chat (limited terminals, pipes)"),
    continue_last: bool = typer.Option(False, "--continue", "-c"),
) -> None:
    """Interactive chat (same UI as `mydevagent`; --plain for the simple version)."""
    if not plain and sys.stdin.isatty():
        from .tui import run_tui

        run_tui(profile, continue_last=continue_last)
        return
    orch = _orchestrator(profile)
    history: list[dict[str, Any]] = []
    files: dict[str, str] = {}
    images: list[str] = []
    show_thinking = False
    model, _ = orch.settings.resolve_model("main")
    console.print(f"[bold cyan]MyDevAgent[/bold cyan] · profile [bold]{orch.settings.profile}[/bold] · "
                  f"model {model} · 15 agents")
    console.print(CHAT_HELP)
    while True:
        try:
            text = console.input("\n[bold green]› [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        cmd, _, arg = text.partition(" ")
        if cmd in ("/exit", "/quit"):
            break
        if cmd in ("/fast", "/balanced", "/deep", "/auto") and not arg:
            mode = cmd[1:]
            console.print(f"[dim]mode: {mode}[/dim]")
            continue
        if cmd == "/reset":
            history, files, images = [], {}, []
            console.print("[dim]conversation cleared[/dim]")
            continue
        if cmd == "/think":
            show_thinking = not show_thinking
            console.print(f"[dim]show reasoning: {show_thinking}[/dim]")
            continue
        if cmd == "/agents":
            agents()
            continue
        if cmd == "/files":
            console.print("[dim]" + (", ".join(files) or "no files attached") + "[/dim]")
            continue
        if cmd == "/clear-files":
            files, images = {}, []
            continue
        if cmd in ("/file", "/image"):
            path = Path(arg).expanduser()
            if not path.is_file():
                console.print(f"[red]file not found: {path}[/red]")
                continue
            if cmd == "/file":
                files.update(_read_files([path]))
            else:
                images.extend(_images([path]))
            console.print(f"[dim]attached: {path}[/dim]")
            continue

        answer = []
        try:
            for chunk in orch.run(text, history=history, files=files, images=images, mode=mode,
                                  on_event=_event_printer(console), show_thinking=show_thinking):
                answer.append(chunk)
                console.print(chunk, end="", markup=False, highlight=False, soft_wrap=True)
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/yellow]")
        except Exception as exc:
            _print_error(console, exc, orch.settings)
            continue
        images = []  # images only apply to a single turn
        history += [{"role": "user", "content": text}, {"role": "assistant", "content": "".join(answer)}]


@app.command()
def serve(
    host: str = typer.Option(None, help="Default from settings.yaml (127.0.0.1)"),
    port: int = typer.Option(None, help="Default from settings.yaml (8000)"),
) -> None:
    """Starts the OpenAI-compatible server (for VS Code/Continue, Cursor, Aider, Cline...)."""
    from .server import serve as run_server

    run_server(host, port)


@app.command()
def bridge(
    profile: str = typer.Option(None, "--profile", "-p", help="cpu | gpu8 | gpu16 | gpu24"),
    permissions: str = typer.Option("ask", "--permissions", help="ask | auto-edit | plan | auto"),
) -> None:
    """Bridge for MyDevAgent Studio: JSON over stdin/stdout in the current folder (no need to use it by hand)."""
    from .bridge import main as run_bridge

    run_bridge(profile, permission=permissions)


@app.command()
def index(
    path: Path = typer.Argument(Path("."), help="Root of the project to index"),
    no_embeddings: bool = typer.Option(False, help="Lexical index only (no embedding model)"),
) -> None:
    """Indexes the codebase for local RAG (saved in <path>/.mydevagent/index.json)."""
    from .config import load_settings
    from .llm import build_llm
    from .tools.rag import CodeIndex

    settings = load_settings(overrides={"tools": {"filesystem": {"root": str(path.resolve())}}})
    idx = CodeIndex.for_workspace(settings, build_llm(settings))
    with console.status("indexing…"):
        stats = idx.build(use_embeddings=not no_embeddings)
    kind = "semantic" if stats["embedded"] else "lexical (embeddings not available)"
    console.print(f"[green]✓[/green] {stats['chunks']} chunks indexed · {kind} index · {idx.path}")


@app.command()
def agents() -> None:
    """Lists the agents (15 core + 20 ultra-deep)."""
    from .config import get_settings
    from .registry import load_registry

    registry = load_registry(get_settings())
    table = Table(title=f"MyDevAgent — {len(registry)} agents (15 core + {len(registry.ultra())} ultra-deep)",
                  show_lines=False)
    for col in ("#", "Agent", "Group", "Stage", "Tier", "Aliases"):
        table.add_column(col)
    for a in registry:
        table.add_row(str(a.id), a.name, "core" if a.group == "core" else "ultra", a.stage, a.tier,
                      " ".join("@" + x for x in a.aliases))
    console.print(table)


@app.command()
def route(request: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """Shows which mode and which agents would be used (no LLM call)."""
    from .config import get_settings
    from .registry import load_registry
    from .router import Router

    settings = get_settings()
    r = Router(settings, load_registry(settings)).route(request)
    data = {"mode": r.mode, "agents": r.agents, "primary": r.primary, "specialists": r.specialists,
            "gate": r.gate, "research": r.research, "reasons": r.reasons, "scores": r.scores}
    if as_json:
        console.print_json(json.dumps(data))
    else:
        console.print(f"[bold]{r.mode}[/bold] → {' → '.join(r.agents)}\n[dim]{'; '.join(r.reasons)}[/dim]")


@app.command()
def bench(
    profile: str = typer.Option(None, "--profile", "-p"),
    tiers: str = typer.Option("main,fast", help="tiers to measure, comma-separated"),
) -> None:
    """Measures first token and speed (tokens/s) of the models on your hardware and recommends a profile."""
    import time

    from .config import load_settings
    from .llm import build_llm

    settings = load_settings(overrides={"profile": profile} if profile else None)
    llm = build_llm(settings)
    prompt = [{"role": "system", "content": "You are a concise senior engineer."},
              {"role": "user", "content": "Write a Python function that returns the n-th Fibonacci number "
                                          "iteratively, with type hints and a docstring. Code only."}]
    table = Table(title=f"Benchmark · profile {settings.profile}")
    for col in ("tier", "model", "first token", "tokens/s", "total"):
        table.add_column(col)
    speeds: dict[str, float] = {}
    for tier in [t.strip() for t in tiers.split(",") if t.strip()]:
        model = settings.resolve_model(tier)[0]
        try:
            llm.complete([{"role": "user", "content": "ok"}], tier=tier, max_tokens=1)  # warmup/loading
            start = time.perf_counter()
            first = None
            text = ""
            for chunk in llm.stream(prompt, tier=tier, max_tokens=256, temperature=0.0):
                if first is None:
                    first = time.perf_counter() - start
                text += chunk
            total = time.perf_counter() - start
            tokens = max(1, len(text) // 4)
            gen_time = max(1e-3, total - (first or 0))
            speeds[tier] = tokens / gen_time
            table.add_row(tier, model, f"{(first or 0):.2f}s", f"{speeds[tier]:.1f}", f"{total:.1f}s")
        except Exception as exc:
            table.add_row(tier, model, "[red]error[/red]", "-", f"[red]{type(exc).__name__}[/red]")
    console.print(table)
    main_speed = speeds.get("main")
    if main_speed is None:
        console.print("[yellow]No measurement for the main model: check `mydevagent doctor`.[/yellow]")
        return
    order = ["cpu", "gpu8", "gpu16", "gpu24"]
    idx = order.index(settings.profile) if settings.profile in order else 1
    if main_speed < 8 and idx > 0:
        console.print(f"[yellow]Slow ({main_speed:.0f} tok/s): try the [bold]{order[idx - 1]}[/bold] profile "
                      "or /fast for simple requests.[/yellow]")
    elif main_speed > 45 and idx < len(order) - 1:
        console.print(f"[green]Fast ({main_speed:.0f} tok/s): you can try the [bold]{order[idx + 1]}"
                      "[/bold] profile for more quality.[/green]")
    else:
        console.print(f"[green]The {settings.profile} profile is a good fit for this PC ({main_speed:.0f} tok/s).[/green]")
    console.print("[dim]Token estimate ≈ characters/4. Speed tips: docs/PERFORMANCE.md[/dim]")


@app.command()
def update() -> None:
    """Updates MyDevAgent (git pull; dependencies only if they changed). Models and settings are kept."""
    from .update import update as run_update

    with console.status("Updating MyDevAgent…"):
        result = run_update()
    console.print(f"[{'green' if result.ok else 'red'}]{'✓' if result.ok else '✗'}[/] {escape(result.message)}")
    for change in result.changes:
        console.print(f"  [dim]• {escape(change)}[/]")
    if not result.ok:
        raise typer.Exit(1)


@app.command()
def doctor(profile: str = typer.Option(None, "--profile", "-p")) -> None:
    """Checks LLM backends, models, connection, web search, sandbox and RAG index."""
    import os

    from .config import load_settings
    from .health import (
        OPTIONAL_TIERS,
        check_backends,
        detect_hardware,
        is_ollama,
        recommend_profile,
        start_hint,
    )
    from .tools.connectivity import Connectivity
    from .tools.sandbox import Sandbox
    from .tools.web_search import PROVIDERS

    settings = load_settings(overrides={"profile": profile} if profile else None)
    ok = "[green]✓[/green]"
    ko = "[red]✗[/red]"
    warn = "[yellow]![/yellow]"
    console.print(f"[bold]Profile[/bold]: {settings.profile}")

    health = check_backends(settings, timeout=3)
    for url, reachable in health.reachable.items():
        if reachable:
            console.print(f"{ok} backend reachable: {url}")
        else:
            console.print(f"{ko} backend not reachable: {url} — {start_hint(url)}")
    for status in health.tiers:
        if status.installed is None:
            continue
        optional = " [dim](optional)[/dim]" if status.tier in OPTIONAL_TIERS else ""
        console.print(f"  {ok if status.installed else warn} {status.tier:<9} {status.model}{optional}")
    missing = [s.model for s in health.missing()]
    if missing and is_ollama(settings.backends[settings.default_backend].base_url):
        console.print("  [dim]download the missing models:[/dim] " + " && ".join(f"ollama pull {m}"
                                                                           for m in dict.fromkeys(missing)))
    hw = detect_hardware()
    best = recommend_profile(hw)
    same = best == settings.profile
    console.print(f"{ok if same else warn} hardware: {hw.describe()} → recommended profile [bold]{best}[/bold]"
                  + ("" if same else f" (you are using {settings.profile}: `mydevagent -p {best}`)"))

    online = Connectivity(settings.tools.connectivity).online()
    console.print(f"{ok if online else warn} internet: {'online' if online else 'offline (web search disabled)'}")
    configured = [name for name in settings.tools.web.providers
                  if name in PROVIDERS and PROVIDERS[name](5).available()]
    console.print(f"{ok if configured else warn} web search: {', '.join(configured) or 'no provider'}"
                  + ("" if configured else " — set TAVILY_API_KEY or `pip install ddgs`"))
    backend = Sandbox(settings.tools.sandbox).backend()
    if backend == "docker":
        console.print(f"{ok} sandbox: docker")
    else:
        hint = "install Docker" if not shutil.which("docker") else "enable tools.sandbox"
        console.print(f"{warn} sandbox: {backend or 'not available'} — {hint}")
    root = Path(settings.tools.filesystem.root).resolve()
    rag_index = root / settings.tools.rag.index_dir / "index.json"
    console.print(f"{ok if rag_index.is_file() else warn} RAG index: "
                  f"{rag_index if rag_index.is_file() else 'missing — run `mydevagent index`'}")
    if os.environ.get("MYDEVAGENT_API_KEY"):
        console.print(f"{ok} server: protected by MYDEVAGENT_API_KEY")


if __name__ == "__main__":
    app()
