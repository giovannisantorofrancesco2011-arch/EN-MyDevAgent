import sys

import pytest

from mydevagent import hooks
from mydevagent.agent import PermissionPolicy
from mydevagent.agent.runner import AgentRunner
from mydevagent.orchestrator import Orchestrator
from mydevagent.subagents import load_subagents
from tests.test_agent import ScriptedLLM, T


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "explorer.md").write_text(
        "---\nname: explorer\ndescription: Searches the code and summarizes.\ntools: Read, Grep, Glob\n"
        "model: haiku\n---\nYou are an explorer: read and summarize, change nothing.\n")
    (agents / "writer.md").write_text("---\ndescription: Writes code.\n---\nWrite the requested code.\n")
    return root


class SubLLM(ScriptedLLM):
    """Separate scripts for the main agent and for subagents (recognized by their prompt)."""

    def __init__(self, main, sub):
        super().__init__(steps=main)
        self.sub = sub

    def complete(self, messages, **kwargs):
        system = messages[0]["content"]
        if system.startswith(("You are an explorer", "Write the requested code")):
            self.calls.append({"role": "sub", "tier": kwargs.get("tier"), "messages": [dict(m) for m in messages]})
            steps, self.steps = self.steps, self.sub
            try:
                return super().complete(messages, **kwargs)
            finally:
                self.sub, self.steps = self.steps, steps
        return super().complete(messages, **kwargs)


def test_load_subagents(project):
    (project / ".claude" / "agents" / "json.md").write_text('---\ntools: ["Read", "Bash(git:*)"]\n---\nx\n')
    agents = load_subagents(project)
    assert agents["json"].allowed() == {"read_file", "bash", "run_tests"}
    assert agents["explorer"].allowed() == {"read_file", "grep", "list_files"}
    assert agents["explorer"].tier == "fast" and agents["writer"].allowed() is None


def test_main_agent_delegates_to_subagent(project, settings, tmp_path):
    stop_log = tmp_path / "stop.log"
    llm = SubLLM(
        main=[T("task", agent="explorer", prompt="Find where the numbers are added"), "add is in src/calc.py."],
        sub=[T("grep", pattern="def add"), T("write_file", path="x.py", content="no"), "add is in src/calc.py:1"])
    session = hooks.Hooks(project, [hooks.Hook("SubagentStop", "", f'"{sys.executable}" -c "open(r\'{stop_log}\', '
                                                                    f'\'a\').write(\'x\')"')])
    events = []
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(mode="auto", root=project),
                         hooks=session)
    out = "".join(runner.run("where are the numbers added?", on_event=events.append))
    assert out.startswith("add is in src/calc.py.")

    main_system = llm.calls[0]["messages"][0]["content"]
    assert "# Sub-agents" in main_system and "- explorer: Searches the code and summarizes." in main_system
    subs = [c for c in llm.calls if c["role"] == "sub"]
    assert subs[0]["tier"] == "fast" and subs[0]["messages"][1]["content"] == "Find where the numbers are added"
    sub_system = subs[0]["messages"][0]["content"]
    assert '"name": "task"' not in sub_system and '"name": "write_file"' not in sub_system  # limited tools
    assert "unknown tool 'write_file'" in str(subs[2]["messages"][-1]["content"])
    assert not (project / "x.py").exists() and stop_log.read_text() == "x"
    report = str(llm.calls[-1]["messages"][-1]["content"])
    assert "Report from sub-agent explorer:\nadd is in src/calc.py:1" in report
    assert any(e["type"] == "agent_end" and e["name"] == "Agent explorer" for e in events)


def test_subagent_changes_count_for_the_main_agent(project, settings):
    llm = SubLLM(main=[T("task", subagent_type="writer", prompt="Create src/sub.py with sub(a, b)"), "Done."],
                 sub=[T("write_file", path="src/sub.py", content="def sub(a, b):\n    return a - b\n"), "Created."])
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(mode="auto", root=project))
    out = "".join(runner.run("/fast add sub in src/sub.py"))
    assert (project / "src" / "sub.py").exists() and "Files changed: src/sub.py" in out
    assert "(files changed by writer: src/sub.py)" in str(llm.calls[-1]["messages"][-1]["content"])


def test_unknown_subagent(project, settings):
    llm = SubLLM(main=[T("task", agent="nope", prompt="x"), "Ok."], sub=[])
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(root=project))
    "".join(runner.run("what does add do?"))
    assert "unknown sub-agent 'nope'. Available: explorer, writer" in llm.calls[1]["messages"][-1]["content"]


def test_cancel_inside_subagent_stops_everything(project, settings):
    import threading

    cancel = threading.Event()

    class Cancelling(SubLLM):
        def complete(self, messages, **kwargs):
            if messages[0]["content"].startswith("You are an explorer"):
                cancel.set()  # the user presses Esc while the subagent is working
            return super().complete(messages, **kwargs)

    llm = Cancelling(main=[T("task", agent="explorer", prompt="search"), "never"], sub=[T("grep", pattern="x"), "x"])
    events = []
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(root=project))
    assert "".join(runner.run("where are they added?", on_event=events.append, cancel=cancel)) == ""
    assert events[-1]["type"] == "cancelled"
