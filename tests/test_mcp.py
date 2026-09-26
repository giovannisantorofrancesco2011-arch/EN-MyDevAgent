import json
import sys
from pathlib import Path

import httpx
import pytest

from mydevagent import hooks, mcp
from mydevagent.agent import PermissionPolicy
from mydevagent.agent.runner import AgentRunner
from mydevagent.orchestrator import Orchestrator
from tests.test_agent import ScriptedLLM, make_tools

FAKE_SERVER = r'''
import json, sys
print("log: starting", flush=True)  # a non-JSON line on stdout: must be ignored
TOOLS = [
    {"name": "add", "description": "Adds two numbers", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                     "required": ["a", "b"]}},
    {"name": "boom", "description": "Always fails", "inputSchema": {"type": "object", "properties": {}}},
]
for line in sys.stdin:
    msg = json.loads(line)
    if "method" not in msg or "id" not in msg:  # notifications, and our reply to the ping
        continue
    method, params = msg["method"], msg.get("params", {})
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}), flush=True)  # request from the server
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "calc", "version": "1"}, "instructions": "Calculator"}
    elif method == "tools/list":
        result = {"tools": TOOLS[:1], "nextCursor": "2"} if "cursor" not in params else {"tools": TOOLS[1:]}
    elif method == "tools/call":
        args = params["arguments"]
        if params["name"] == "add":
            result = {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        else:
            result = {"content": [{"type": "text", "text": "broken"}], "isError": True}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no"}}),
              flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    return root


@pytest.fixture
def calc(tmp_path, project):
    script = tmp_path / "calc_server.py"
    script.write_text(FAKE_SERVER)
    config = mcp.ServerConfig("calc", {"command": sys.executable, "args": [str(script)]}, "user")
    manager = mcp.McpManager(project, {"calc": config})
    yield manager
    manager.close()


def test_config_sources_and_trust(project, tmp_path, monkeypatch):
    home = Path.home()
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"email": "x"}, "tipsHistory": {"a": 1},  # the rest of Claude Code's file
        "mcpServers": {"github": {"command": "npx", "args": ["-y", "server-github"],
                                  "env": {"TOKEN": "${GH_TOKEN:-missing}"}}},
        "projects": {str(project.resolve()): {"mcpServers": {"db": {"type": "http", "url": "http://x/mcp"}}}}}))
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"browser": {"command": "npx",
                                                                              "args": ["playwright-mcp"]}}}))
    plugin = tmp_path / "state" / "plugins" / "docs"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "docs"}))
    (plugin / ".mcp.json").write_text(json.dumps({"docs": {"command": "${CLAUDE_PLUGIN_ROOT}/server"}}))  # without mcpServers

    configs = mcp.server_configs(project)
    assert {n: c.source for n, c in configs.items()} == {
        "db": "claude code", "browser": "project", "github": "claude code", "docs": "plugin docs"}
    assert configs["db"].kind == "http" and configs["browser"].describe() == "npx playwright-mcp"
    monkeypatch.setenv("GH_TOKEN", "secret")
    assert mcp.expand(configs["github"].config)["env"]["TOKEN"] == "secret"
    monkeypatch.delenv("GH_TOKEN")
    assert mcp.expand(configs["github"].config)["env"]["TOKEN"] == "missing"

    assert [c.name for c in mcp.untrusted(project)] == ["browser"]
    assert "browser" not in mcp.McpManager(project).servers  # the project waits for your OK
    mcp.allow(project)
    assert not mcp.untrusted(project) and "browser" in mcp.McpManager(project).servers


def test_stdio_server(calc):
    calc.connect_all()
    server = calc.servers["calc"]
    assert not server.error and [t["name"] for t in server.tools] == ["add", "boom"]  # with pagination
    assert server.instructions == "Calculator"
    assert server.call("add", {"a": 2, "b": 3}) == "5"
    assert server.call("boom", {}) == "ERROR: broken"
    assert "- calc: add, boom" in calc.prompt()
    assert "- add(a: number, b: number): Adds two numbers" in calc.list_tools(server)


def test_broken_server_reports_error(project, tmp_path):
    config = mcp.ServerConfig("broken", {"command": sys.executable, "args": ["-c", "import sys; "
                                        "print('token is missing', file=sys.stderr); sys.exit(1)"]}, "user")
    manager = mcp.McpManager(project, {"broken": config})
    manager.connect_all()
    assert "token is missing" in manager.servers["broken"].error and manager.prompt() == ""
    missing = mcp.McpManager(project, {"x": mcp.ServerConfig("x", {"command": "does-not-really-exist"}, "user")})
    missing.connect_all()
    assert missing.servers["x"].error


def test_agent_mcp_tool_permissions_and_hooks(calc, project, tmp_path):
    asked = []
    tools = make_tools(project, mode="ask", approver=lambda req: (asked.append(req.summary), ("yes", ""))[1])
    tools.mcp = calc
    assert "mcp" in [s["name"] for s in tools.specs()]
    assert "- boom(): Always fails" in tools.execute("mcp", {"server": "calc"})
    assert tools.execute("mcp", {"server": "calc", "tool": "add", "arguments": '{"a": 1, "b": 1}'}) == "2"
    assert asked == ['MCP(calc.add {"a": 1, "b": 1})']
    assert tools.execute("mcp", {"server": "nope"}).startswith("ERROR: unknown MCP server 'nope'")

    plan = make_tools(project, mode="plan")
    plan.mcp = calc
    assert plan.execute("mcp", {"server": "calc", "tool": "add", "arguments": {"a": 2, "b": 2}}) == "4"
    assert plan.execute("mcp", {"server": "calc", "tool": "boom"}).startswith("DENIED")  # not read-only

    seen = tmp_path / "seen.json"
    hook = f'"{sys.executable}" -c "import sys; open(r\'{seen}\', \'w\').write(sys.stdin.read())"'
    auto = make_tools(project, mode="auto")
    auto.mcp = calc
    auto.hooks = hooks.Hooks(project, [hooks.Hook("PreToolUse", "mcp__calc__.*", hook)])
    auto.execute("mcp", {"server": "calc", "tool": "add", "arguments": {"a": 5, "b": 5}})
    event = json.loads(seen.read_text())
    assert event["tool_name"] == "mcp__calc__add" and event["tool_input"] == {"a": 5, "b": 5}


def test_runner_lists_servers(calc, project, settings):
    calc.connect_all()
    llm = ScriptedLLM(steps=["Ok."])
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(root=project), mcp=calc)
    "".join(runner.run("what is 2+3?"))
    system = llm.calls[0]["messages"][0]["content"]
    assert "# MCP servers" in system and "- calc: add, boom" in system and '"name": "mcp"' not in system


def test_http_transport_json_and_sse():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((body.get("method"), request.headers.get("mcp-session-id")))
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
                                  headers={"Mcp-Session-Id": "s1"})
        if body["method"] == "tools/list":
            data = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "search"}]}})
            return httpx.Response(200, text=f"event: message\ndata: {data}\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(401)

    server = mcp.Server(mcp.ServerConfig("web", {"type": "http", "url": "http://x/mcp"}, "user"), Path("."))
    real = mcp.HttpTransport
    mcp.HttpTransport = lambda url, headers: real(url, headers, httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        server.connect()
    finally:
        mcp.HttpTransport = real
    assert not server.error and [t["name"] for t in server.tools] == ["search"]
    assert seen == [("initialize", None), ("notifications/initialized", "s1"), ("tools/list", "s1")]
    with pytest.raises(mcp.McpError, match="authentication"):
        server.call("search", {})
