import pytest

from mydevagent.config import SandboxConfig, WebConfig
from mydevagent.tools import Toolbox
from mydevagent.tools.filesystem import Workspace, WorkspaceError
from mydevagent.tools.rag import CodeIndex
from mydevagent.tools.sandbox import Sandbox
from mydevagent.tools.web_fetch import UnsafeURLError, check_url, html_to_text
from mydevagent.tools.web_search import WebSearch


def test_workspace_blocks_traversal(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    ws = Workspace(tmp_path)
    assert ws.read("a.txt") == "hello"
    with pytest.raises(WorkspaceError):
        ws.read("../../etc/passwd")
    with pytest.raises(WorkspaceError):
        ws.write("b.txt", "x")  # writing disabled by default


def test_workspace_blocks_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(WorkspaceError):
        Workspace(root).read("link")


def test_workspace_refuses_secret_files(tmp_path):
    (tmp_path / ".env").write_text("KEY=1")
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path).read(".env")


def test_grep_and_list(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def hello():\n    return 1\n")
    ws = Workspace(tmp_path)
    assert "src/m.py:1" in ws.grep(r"def hello")
    assert "src/" in ws.list()


def test_ssrf_protection():
    with pytest.raises(UnsafeURLError):
        check_url("http://127.0.0.1:11434/api/tags")
    with pytest.raises(UnsafeURLError):
        check_url("file:///etc/passwd")
    check_url("http://127.0.0.1/", allow_private=True)


def test_html_to_text():
    assert html_to_text("<html><script>x()</script><p>Hi &amp; bye</p></html>") == "Hi & bye"


def test_web_search_offline(settings):
    class Offline:
        def online(self):
            return False

    response = WebSearch(WebConfig(), Offline()).search("fastapi latest version")
    assert response.offline and not response.results


def test_sandbox_local_runs_and_times_out():
    sb = Sandbox(SandboxConfig(backend="local", allow_unsafe_local=True, timeout_s=3))
    ok = sb.run("from pkg.mod import f\nassert f() == 2\nprint('SELF-CHECK OK')",
                files={"pkg/mod.py": "def f():\n    return 2\n"})
    assert ok.ok and "SELF-CHECK OK" in ok.stdout
    bad = sb.run("raise SystemExit(3)")
    assert not bad.ok and bad.exit_code == 3
    slow = sb.run("while True: pass")
    assert slow.timed_out


def test_sandbox_disabled_without_opt_in():
    sb = Sandbox(SandboxConfig(backend="local", allow_unsafe_local=False))
    assert sb.run("print(1)").skipped


def test_sandbox_rejects_path_escape(tmp_path):
    sb = Sandbox(SandboxConfig(backend="local", allow_unsafe_local=True, timeout_s=5))
    res = sb.run("import os\nprint(sorted(os.listdir('.')))", files={"../evil.py": "x", "ok.py": "y"})
    assert "evil.py" not in res.stdout and "ok.py" in res.stdout


def test_toolbox_errors_are_strings(settings):
    tb = Toolbox(settings)
    assert tb.execute("does_not_exist", {}).startswith("ERROR")
    assert tb.execute("read_file", {"path": "../../etc/passwd"}).startswith("ERROR")
    assert "OFFLINE" in tb.execute("web_search", {"query": "x"})


def test_rag_lexical_and_embedded(settings, fake_llm, tmp_path):
    (tmp_path / "auth.py").write_text("def verify_password(hashed, plain):\n    return check(hashed, plain)\n")
    (tmp_path / "math_utils.py").write_text("def add(a, b):\n    return a + b\n")
    lexical = CodeIndex.for_workspace(settings, None)
    assert lexical.build(use_embeddings=False)["chunks"] == 2
    assert lexical.search("password verification")[0][1].path == "auth.py"
    semantic = CodeIndex.for_workspace(settings, fake_llm)
    stats = semantic.build()
    assert stats["embedded"] == 2
    assert semantic.search("verify_password hashed plain")[0][1].path == "auth.py"
