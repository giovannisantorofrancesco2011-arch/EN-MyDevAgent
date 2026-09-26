import json
import sys
from pathlib import Path

import pytest

from mydevagent import hooks
from mydevagent.agent import AgentLoop, PermissionPolicy
from mydevagent.agent.runner import AgentRunner
from mydevagent.orchestrator import Orchestrator
from tests.test_agent import ScriptedLLM, make_tools


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return root


def script(folder: Path, name: str, body: str) -> str:
    """A hook written in Python (works the same on Windows): reads the event from stdin."""
    path = folder / name
    path.write_text("import json, sys\nevent = json.load(sys.stdin)\n" + body)
    return f'"{sys.executable}" "{path}"'


def settings_file(path: Path, event: str, command: str, matcher: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {"hooks": {}}
    data["hooks"].setdefault(event, []).append({"matcher": matcher, "hooks": [{"type": "command",
                                                                               "command": command}]})
    path.write_text(json.dumps(data))


def test_sources_trust_and_plugins(project, tmp_path):
    home = Path.home()
    settings_file(home / ".claude" / "settings.json", "PostToolUse", "echo cc", "Edit|Write")
    settings_file(project / ".mydevagent" / "settings.json", "SessionStart", "echo project")
    plugin = tmp_path / "state" / "plugins" / "fmt"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "fmt"}))
    settings_file(plugin / "hooks" / "hooks.json", "PreToolUse", "${CLAUDE_PLUGIN_ROOT}/check.sh", "Bash")

    found = {(h.source, h.event) for h in hooks.load_hooks(project)}
    assert found == {("claude code", "PostToolUse"), ("plugin fmt", "PreToolUse")}  # the project waits for the OK
    assert [h.command for h in hooks.untrusted(project)] == ["echo project"]
    hooks.allow(project)
    assert not hooks.untrusted(project) and len(hooks.load_hooks(project)) == 3
    settings_file(project / ".mydevagent" / "settings.json", "Stop", "echo new")  # changed: asks again
    assert len(hooks.untrusted(project)) == 2 and len(hooks.load_hooks(project)) == 2

    (home / ".mydevagent").mkdir()
    (tmp_path / "state" / "settings.json").write_text(json.dumps({"disableAllHooks": True}))
    assert hooks.load_hooks(project) == []


def test_tool_hooks_block_and_give_feedback(project, tmp_path):
    seen = tmp_path / "seen.json"
    pre = script(tmp_path, "pre.py", "if 'rm ' in event['tool_input']['command']:\n"
                                     "    print('no rm', file=sys.stderr); sys.exit(2)\n")
    post = script(tmp_path, "post.py", f"open({str(seen)!r}, 'w').write(json.dumps(event))\n"
                                       "print(json.dumps({'decision': 'block', 'reason': 'format the file'}))\n")
    tools = make_tools(project, mode="auto")
    tools.hooks = hooks.Hooks(project, [hooks.Hook("PreToolUse", "Bash", pre),
                                        hooks.Hook("PostToolUse", "Edit|Write", post)])
    assert tools.execute("bash", {"command": "rm -rf src"}) == "DENIED by a hook: no rm"
    assert (project / "src").is_dir()
    assert tools.execute("bash", {"command": "echo ok"}).startswith("exit code 0")

    result = tools.execute("write_file", {"path": "src/new.py", "content": "x = 1\n"})
    assert result.startswith("created src/new.py") and result.endswith("[hook] format the file")
    event = json.loads(seen.read_text())
    assert event["hook_event_name"] == "PostToolUse" and event["tool_name"] == "Write"
    assert event["tool_input"]["file_path"] == str((project / "src" / "new.py").resolve())
    assert event["tool_response"]["success"] is True


def test_stop_hook_asks_to_continue(project, tmp_path):
    stop = script(tmp_path, "stop.py", "if not event['stop_hook_active']:\n"
                                       "    print('tests are missing', file=sys.stderr); sys.exit(2)\n")
    llm = ScriptedLLM(steps=["Done.", "Tests added, now I'm done."])
    tools = make_tools(project)
    tools.hooks = hooks.Hooks(project, [hooks.Hook("Stop", "", stop)])
    result = AgentLoop(llm, tools, system="# Role: t").run("add sub")
    assert result.text == "Tests added, now I'm done." and result.steps == 2
    assert "A hook asks you to continue: tests are missing" in llm.calls[1]["messages"][-1]["content"]


def test_prompt_and_session_hooks(project, settings, tmp_path):
    submit = script(tmp_path, "submit.py", "if 'password' in event['prompt']:\n"
                                           "    print('no passwords', file=sys.stderr); sys.exit(2)\n"
                                           "print('The current branch is feature/x')\n")
    start = script(tmp_path, "start.py", "print(json.dumps({'hookSpecificOutput': {'hookEventName': "
                                         "'SessionStart', 'additionalContext': 'Always use pnpm'}}))\n")
    session = hooks.Hooks(project, [hooks.Hook("UserPromptSubmit", "", submit),
                                    hooks.Hook("SessionStart", "startup", start)])
    session.start("startup")
    llm = ScriptedLLM(steps=["Ok."])
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(root=project), hooks=session)
    assert "".join(runner.run("what does add do?")).startswith("Ok.")
    system = llm.calls[0]["messages"][0]["content"]
    assert "The current branch is feature/x" in system and "Always use pnpm" in system
    out = "".join(runner.run("what is the db password?"))
    assert out == "⛔ Request blocked by a hook: no passwords" and len(llm.calls) == 1


def test_matcher_uses_claude_code_and_own_names():
    hook = hooks.Hook("PreToolUse", "Edit|Write", "x")
    assert hook.matches("edit_file", "Edit") and not hook.matches("bash", "Bash")
    assert hooks.Hook("PreToolUse", "bash", "x").matches("bash", "Bash")
    assert hooks.Hook("PreToolUse", "*", "x").matches("grep", "Grep")


def test_unsupported_hooks_are_listed_but_not_run(tmp_path):
    marker = tmp_path / "ran"
    config = {"hooks": {"Stop": [{"hooks": [{"command": f"echo x > {marker}", "asyncRewake": True}]}],
                        "Notification": [{"hooks": [{"command": f"echo x > {marker}"}]}]}}
    parsed = hooks.parse(config, "plugin sg")
    assert [h.unsupported for h in parsed] == ["not supported yet: asyncRewake", "event not supported yet"]
    hooks.Hooks(tmp_path, parsed).run("Stop")
    assert not marker.exists()
