"""Website preview: opens a localhost page in a headless browser (an already installed Chrome, Edge or
Chromium), takes a screenshot and collects console errors, missing files and visible text. The
screenshot is described by the `vision` model, so the agent "sees" the site it built.

- a project HTML file is served by a small server on 127.0.0.1 (no hidden files or secrets)
- a site with its own server (npm run dev, uvicorn…) is opened at its url; the `start` command launches
  it and it keeps running until MyDevAgent exits
"""

from __future__ import annotations

import atexit
import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx

from .filesystem import Workspace
from .vision import describe_images, image_to_data_url
from .web_fetch import html_to_text

LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
CONSOLE_RE = re.compile(r'CONSOLE[:(]?\d*\)?\] "(.*)", source: (\S*) \((\d+)\)')
BROWSER_NAMES = ("msedge", "chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
WINDOWS_BROWSERS = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")
MAC_BROWSERS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium")
BROWSER_TIMEOUT = 45
TEXT_CHARS = 1500

_servers: dict[Path, ThreadingHTTPServer] = {}
_processes: dict[tuple[str, str], subprocess.Popen] = {}
_lock = threading.Lock()


def find_browser() -> str | None:
    """An already installed Chrome, Edge or Chromium (MYDEVAGENT_BROWSER to pick one)."""
    if os.environ.get("MYDEVAGENT_BROWSER"):
        return os.environ["MYDEVAGENT_BROWSER"]
    for name in BROWSER_NAMES:
        if found := shutil.which(name):
            return found
    candidates = list(MAC_BROWSERS)
    for base in filter(None, (os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"))):
        candidates += [str(Path(base) / rel) for rel in WINDOWS_BROWSERS]
    return next((c for c in candidates if Path(c).is_file()), None)


def is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in LOCAL_HOSTS or host.endswith(".localhost")


# ------------------------------------------------------------ project files
class _Files(SimpleHTTPRequestHandler):
    """Project files on 127.0.0.1: no hidden files (.git, .env) or keys; remembers 404s."""

    def send_head(self):
        path = unquote(urlparse(self.path).path)
        if any(part.startswith(".") for part in path.split("/")) or Workspace.is_secret(Path(path)):
            self.send_error(404)
            return None
        return super().send_head()

    def send_error(self, code, message=None, explain=None):
        if code == 404:
            self.server.missing.add(unquote(urlparse(self.path).path))
        super().send_error(code, message, explain)

    def log_message(self, *args) -> None:
        pass


def serve(root: Path) -> str:
    """Address of the project file server: one per folder, running until exit."""
    root = Path(root).resolve()
    with _lock:
        server = _servers.get(root)
        if server is None:
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Files, directory=str(root)))
            server.daemon_threads = True
            server.missing = set()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            _servers[root] = server
    return f"http://127.0.0.1:{server.server_address[1]}"


# ------------------------------------------------------------ project server
def _tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def start_server(root: Path, command: str, url: str, timeout: float = 60) -> str:
    """Start the project server (only once) and wait for `url` to answer. "" when it is ready."""
    root = Path(root).resolve()
    log = root / ".mydevagent" / "preview-server.log"
    with _lock:
        proc = _processes.get((str(root), command))
        if proc is None or proc.poll() is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
                     else {"start_new_session": True})  # so it shuts down along with all its children
            with open(log, "wb") as out:
                proc = subprocess.Popen(command, shell=True, cwd=root, stdout=out, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, **group)
            _processes[(str(root), command)] = proc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return f"`{command}` exited with code {proc.returncode}:\n{_tail(log)}"
        try:
            httpx.get(url, timeout=2)
            return ""
        except httpx.HTTPError:
            time.sleep(0.5)
    return f"nothing answered at {url} after {timeout:.0f}s. Server output:\n{_tail(log)}"


def stop_all() -> None:
    for proc in _processes.values():
        if proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            else:
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, signal.SIGTERM)
    _processes.clear()


atexit.register(stop_all)


# --------------------------------------------------------------------- browser
def _console(logs: str) -> list[str]:
    seen: list[str] = []
    for message, source, line in CONSOLE_RE.findall(logs):
        entry = f"{message} ({source.rsplit('/', 1)[-1] or source}:{line})"
        if entry not in seen:
            seen.append(entry)
    return seen


def capture(browser: str, url: str, screenshot: Path) -> tuple[str, list[str]]:
    """Screenshot the page and return (html after JavaScript, console messages)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                "--no-default-browser-check", f"--user-data-dir={profile}", "--window-size=1280,800",
                "--virtual-time-budget=3000"]
        if sys.platform.startswith("linux") and os.geteuid() == 0:
            args.append("--no-sandbox")  # Chrome won't start as root without it
        # the console: on stderr, or (Windows) in the profile's chrome_debug.log
        shot = subprocess.run(args + ["--enable-logging=stderr", "--v=0", f"--screenshot={screenshot}", url],
                              capture_output=True, text=True, errors="replace", timeout=BROWSER_TIMEOUT)
        dom = subprocess.run(args + ["--enable-logging", "--v=0", "--dump-dom", url],
                             capture_output=True, text=True, errors="replace", timeout=BROWSER_TIMEOUT)
        debug_log = Path(profile) / "chrome_debug.log"
        logs = shot.stderr + dom.stderr + (debug_log.read_text(errors="replace") if debug_log.is_file() else "")
    return dom.stdout, _console(logs)


def preview(root: Path, *, url: str = "", path: str = "", start: str = "", look: str = "", llm=None) -> str:
    browser = find_browser()
    if not browser:
        return ("ERROR: no Chrome, Edge or Chromium found for the preview. Install one of them, or set "
                "MYDEVAGENT_BROWSER to the browser path.")
    root = Path(root).resolve()
    if start:
        problem = start_server(root, start, url)
        if problem:
            return f"ERROR: the server did not start: {problem}"
    server = None
    if not url:
        page = (path or "index.html").replace("\\", "/").lstrip("/")
        if not (root / page).is_file():
            return f"ERROR: {page} not found in the project (give `path`, or `url` for a site with its own server)."
        url = f"{serve(root)}/{quote(page)}"
        server = _servers[root]
        server.missing.clear()
    screenshot = root / ".mydevagent" / "preview.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.unlink(missing_ok=True)
    try:
        html, console = capture(browser, url, screenshot)
    except subprocess.TimeoutExpired:
        return f"ERROR: the browser did not finish loading {url} in {BROWSER_TIMEOUT}s."
    except OSError as exc:
        return f"ERROR: cannot start the browser ({browser}): {exc}"
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    lines = [f"Preview of {url}" + (" (screenshot: .mydevagent/preview.png)" if screenshot.is_file() else ""),
             f"Title: {title.group(1).strip() if title else '(none)'}",
             "Console: " + ("no messages" if not console else "\n" + "\n".join(f"- {c}" for c in console[:20]))]
    missing = sorted(server.missing - {"/favicon.ico"}) if server is not None else []  # the browser always asks for the icon
    if missing:
        lines.append("Missing files (404): " + ", ".join(missing[:20]))
    if screenshot.is_file() and llm is not None:
        hint = f"This is a screenshot of the web page {url}." + (f" Check especially: {look}" if look else "")
        seen = describe_images(llm, [image_to_data_url(screenshot)], hint)
        lines.append(f"How it looks (vision model):\n{seen}" if seen else "How it looks: (no description)")
    elif not screenshot.is_file():
        lines.append("Screenshot: failed (the page may not have loaded)")
    text = html_to_text(html).strip()
    lines.append("Visible text:\n" + (text[:TEXT_CHARS] + (" …" if len(text) > TEXT_CHARS else "") if text
                                      else "(empty page)"))
    return "\n".join(lines)
