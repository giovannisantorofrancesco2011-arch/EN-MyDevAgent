import glob
import os
import socket
import sys

import httpx
import pytest

from mydevagent.agent import AgentTools, CheckpointStore, PermissionPolicy
from mydevagent.llm import Completion
from mydevagent.tools import preview

BROWSER = os.environ.get("MYDEVAGENT_BROWSER") or preview.find_browser() or next(iter(sorted(glob.glob(
    os.path.join(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent"), "chromium-*", "chrome-linux", "chrome")))),
    None)


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "website"
    root.mkdir()
    (root / "index.html").write_text(
        '<!doctype html><title>Test</title><h1>Hello</h1><p id="x">before</p><img src="missing.png">'
        '<script>document.getElementById("x").textContent = "after JS"; foo.bar();</script>')
    (root / ".env").write_text("SECRET=1\n")
    (root / "id_rsa").write_text("key\n")
    return root


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_static_server_hides_secrets(site):
    base = preview.serve(site)
    assert httpx.get(base + "/index.html").status_code == 200
    for secret in ("/.env", "/%2eenv", "/id_rsa", "/.git/config"):
        assert httpx.get(base + secret).status_code == 404
    assert preview.serve(site) == base  # one server per folder


@pytest.mark.skipif(not BROWSER, reason="no Chrome, Edge or Chromium")
def test_preview_sees_the_page(site, monkeypatch):
    monkeypatch.setenv("MYDEVAGENT_BROWSER", BROWSER)
    seen = []

    class Vision:
        def complete(self, messages, **kwargs):
            seen.append(messages)
            return Completion(text="A big \"Hello\" heading on a white background.")

    out = preview.preview(site, llm=Vision())
    assert "Title: Test" in out and "after JS" in out  # the text after JavaScript
    assert "foo is not defined" in out and "/missing.png" in out and "favicon" not in out
    assert "big \"Hello\" heading" in out and (site / ".mydevagent" / "preview.png").is_file()
    assert seen[0][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_preview_tool_rules(site):
    calls = []

    def fake(root, **kwargs):
        calls.append(kwargs)
        return "ok"

    plan = PermissionPolicy(mode="plan", root=site)
    tools = AgentTools(site, plan, CheckpointStore(site), preview=fake)
    assert tools.execute("preview", {"url": "https://example.com"}).startswith("ERROR")  # localhost only
    assert tools.execute("preview", {"start": "npm run dev"}).startswith("ERROR")  # the url is needed too
    assert tools.execute("preview", {"path": "../outside.html"}).startswith("ERROR")
    assert tools.execute("preview", {"url": "localhost:5173", "start": "npm run dev"}).startswith("DENIED")
    assert tools.execute("preview", {"path": "index.html"}) == "ok" and calls[-1]["path"] == "index.html"
    assert tools.execute("preview", {"url": "localhost:5173"}) == "ok" and calls[-1]["url"] == "http://localhost:5173"
    without_browser = AgentTools(site, plan, CheckpointStore(site))
    assert "preview" not in [s["name"] for s in without_browser.specs()]


def test_start_server_waits_and_reports(tmp_path):
    port = free_port()
    started = preview.start_server(tmp_path, f'"{sys.executable}" -m http.server {port} --bind 127.0.0.1',
                                   f"http://127.0.0.1:{port}/", timeout=30)
    assert started == ""
    crashed = preview.start_server(tmp_path, f'"{sys.executable}" -c "print(123 * 3); raise SystemExit(3)"',
                                   f"http://127.0.0.1:{free_port()}/", timeout=30)
    assert "code 3" in crashed and "369" in crashed
    preview.stop_all()
