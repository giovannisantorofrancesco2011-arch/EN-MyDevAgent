import re
from dataclasses import dataclass, field

import pytest

from mydevagent.agent import AgentLoop, AgentTools, CheckpointStore, PermissionPolicy
from mydevagent.agent.context import RepoMap, append_memory, project_context, python_symbols, read_memory
from mydevagent.agent.permissions import is_dangerous
from mydevagent.agent.protocol import parse_text_calls
from mydevagent.agent.runner import AgentRunner
from mydevagent.agent.tools import apply_edit, detect_test_command
from mydevagent.llm import Completion, FakeLLM
from mydevagent.orchestrator import Orchestrator


@dataclass
class ScriptedLLM(FakeLLM):
    """Agent steps from a list; team calls (architect, gates) behave like FakeLLM."""

    steps: list[str] = field(default_factory=list)

    def complete(self, messages, *, tier="main", max_tokens=1024, temperature=0.2, tools=None):
        system = messages[0]["content"]
        if "(agent mode)" in system:
            self.calls.append({"role": "agent", "messages": [dict(m) for m in messages]})
            text = self.steps.pop(0) if self.steps else "Done."
            return Completion(text=text, prompt_tokens=10, completion_tokens=5)
        return super().complete(messages, tier=tier, max_tokens=max_tokens, temperature=temperature)


def T(tool, **args):
    import json
    return f'<tool name="{tool}">{json.dumps(args)}</tool>'


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_calc.py").write_text("from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (root / ".env").write_text("SECRET=1\n")
    return root


def make_tools(root, mode="auto", approver=None, events=None):
    return AgentTools(root, PermissionPolicy(mode=mode, root=root), CheckpointStore(root), approver=approver,
                      emit=(events.append if events is not None else None))


# -------------------------------------------------------------------- protocol
def test_parse_text_calls_tolerant():
    text = 'Reading the file.\n<tool name="read_file">{"path": "a.py",}</tool>\n<tool name="bash">{"command": "ls"'
    visible, calls = parse_text_calls(text)
    assert visible == "Reading the file."
    assert [(c.name, c.args) for c in calls] == [("read_file", {"path": "a.py"}), ("bash", {"command": "ls"})] \
        or calls[1].error  # unclosed block: read, or a clear error
    _, bad = parse_text_calls('<tool name="edit_file">{not json}</tool>')
    assert bad[0].error and "valid JSON" in bad[0].error
    _, multiline = parse_text_calls('<tool name="write_file">{"path": "a.py", "content": "x = 1\ny = 2"}</tool>')
    assert multiline[0].args["content"] == "x = 1\ny = 2"


# ------------------------------------------------------------------------ edit
def test_apply_edit_exact_ambiguous_and_whitespace():
    text = "def f():\n    return 1\n\ndef g():\n    return 1\n"
    assert apply_edit(text, "return 1", "return 2")[1].startswith("old_string matches 2")
    new, err = apply_edit(text, "def g():\n    return 1", "def g():\n    return 2")
    assert err is None and new.endswith("return 2\n")
    new, err = apply_edit(text, "def f():   \n    return 1  ", "def f():\n    return 3")
    assert err is None and "return 3" in new
    new, err = apply_edit("class A:\n    def m(self):\n        pass\n", "def m(self):\n    pass",
                          "def m(self):\n    return 42")
    assert err is None and "        return 42" in new
    _, err = apply_edit(text, "def h():", "x")
    assert "not found" in err and "Similar lines" in err


def test_tools_edit_write_and_checkpoint(project):
    events = []
    tools = make_tools(project, events=events)
    assert "    1→def add(a, b):" in tools.execute("read_file", {"path": "src/calc.py"})
    out = tools.execute("edit_file", {"path": "src/calc.py", "old_string": "def add(a, b):",
                                      "new_string": "def add(a: int, b: int) -> int:"})
    assert out.startswith("updated src/calc.py")
    tools.execute("write_file", {"path": "src/new.py", "content": "X = 1\n"})
    assert (project / "src" / "new.py").read_text() == "X = 1\n"
    assert any(e["type"] == "diff" and e["path"] == "src/calc.py" and e["added"] == 1 for e in events)
    store = tools.checkpoints
    cp, restored = store.undo()
    assert sorted(restored) == ["src/calc.py", "src/new.py"]
    assert (project / "src" / "calc.py").read_text().startswith("def add(a, b):")
    assert not (project / "src" / "new.py").exists()


def test_tools_refuse_secrets_and_escape(project):
    tools = make_tools(project)
    assert tools.execute("read_file", {"path": ".env"}).startswith("ERROR")
    assert tools.execute("write_file", {"path": "../evil.py", "content": "x"}).startswith("ERROR")
    assert tools.execute("nope", {}).startswith("ERROR: unknown tool")


# ----------------------------------------------------------------- permissions
def test_permission_modes(project):
    asked = []

    def approver(req):
        asked.append(req)
        return ("no", "use another name")

    tools = make_tools(project, mode="ask", approver=approver)
    out = tools.execute("write_file", {"path": "a.py", "content": "x"})
    assert out.startswith("DENIED") and "use another name" in out and asked[0].diff.startswith("---")
    assert tools.execute("bash", {"command": "ls"}).startswith("exit code 0")  # read-only: allowed
    plan = make_tools(project, mode="plan")
    assert plan.execute("write_file", {"path": "a.py", "content": "x"}).startswith("DENIED: plan mode")
    assert "edit_file" not in [s["name"] for s in plan.specs()]
    auto = make_tools(project, mode="auto")  # dangerous commands: always confirm
    assert auto.execute("bash", {"command": "rm -rf src"}).startswith("DENIED")
    assert (project / "src").exists()
    assert is_dangerous("git push --force origin main") and not is_dangerous("pytest -q")


def test_always_rule_is_persisted(project):
    tools = make_tools(project, mode="ask", approver=lambda req: ("always", ""))
    tools.execute("bash", {"command": "python -c 'print(1)'"})
    policy = PermissionPolicy(mode="ask", root=project)
    assert policy.decide("bash", {"command": "python -V"}).action == "allow"


def test_web_fetch_pages_and_asks_once_per_site(project):
    asked, fetched = [], []
    page = "x" * 7000 + "DONE"
    tools = AgentTools(project, PermissionPolicy(mode="plan", root=project), CheckpointStore(project),
                       approver=lambda req: asked.append(req) or ("always", ""),
                       web_fetch=lambda url: fetched.append(url) or page)
    first = tools.execute("web_fetch", {"url": "docs.python.org/3/"})
    assert "7004 characters" in first and "offset=6000" in first and "DONE" not in first
    assert asked[0].args["host"] == "docs.python.org"
    assert "DONE" in tools.execute("web_fetch", {"url": "https://docs.python.org/3/", "offset": 6000})
    assert len(asked) == 1 and len(fetched) == 1  # "always" applies to the site; the second chunk comes from the same copy
    tools.execute("web_fetch", {"url": "https://docs.python.org/3/x"})
    assert len(asked) == 1 and len(fetched) == 2
    tools.execute("web_fetch", {"url": "https://docs.python.org.evil.com/"})
    assert len(asked) == 2  # another site: asks again
    assert tools.execute("web_fetch", {"url": "https://*/"}).startswith("ERROR")
    offline = make_tools(project)
    assert "web_fetch" not in [s["name"] for s in offline.specs()]
    assert offline.execute("web_fetch", {"url": "https://example.com"}).startswith("ERROR")


def test_extra_dirs_read_edit_grep_undo(project, tmp_path):
    from mydevagent.agent.context import extra_dirs_context

    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "app.py").write_text("def ping():\n    return 'pong'\n")
    (backend / ".env").write_text("SECRET=1\n")
    (tmp_path / "other.txt").write_text("outside\n")
    tools = AgentTools(project, PermissionPolicy(mode="auto", root=project), CheckpointStore(project),
                       extra_dirs=[backend])
    assert "def ping" in tools.execute("read_file", {"path": "../backend/app.py"})
    assert "../backend/app.py:1:" in tools.execute("grep", {"pattern": "def ping"})
    assert "../backend/app.py" in tools.execute("list_files", {"path": "../backend"})
    assert tools.execute("read_file", {"path": "../backend/.env"}).startswith("ERROR")
    assert tools.execute("read_file", {"path": "../other.txt"}).startswith("ERROR")  # not one of the folders
    tools.checkpoints.begin("edit the backend")
    tools.execute("edit_file", {"path": (backend / "app.py").as_posix(), "old_string": "'pong'",
                                "new_string": "'PONG'"})
    assert "PONG" in (backend / "app.py").read_text() and tools.changed == ["../backend/app.py"]
    assert "+    return 'PONG'" in tools.checkpoints.session_diff()
    tools.checkpoints.undo()
    assert "'pong'" in (backend / "app.py").read_text()
    assert not (project / ".mydevagent" / "backend").exists()  # the copy stays inside the checkpoint
    context = extra_dirs_context(project, [backend])
    assert "## ../backend" in context and "app.py" in context and ".env" not in context


def test_detect_tests_and_run(project):
    assert detect_test_command(project) == "python -m pytest -q"
    assert detect_test_command(project, "## Commands\n- test: `make check`") == "make check"


# --------------------------------------------------------------------- context
def test_repo_map_and_memory(project):
    (project / "web").mkdir()
    (project / "web" / "api.ts").write_text("export async function getUser(id: string) {\n}\nexport class Api {}\n")
    repo_map = RepoMap(project).build("user api")
    assert "web/api.ts" in repo_map and "getUser(id: string)" in repo_map and "def add(a, b)" in repo_map
    assert repo_map.index("web/api.ts") < repo_map.index("src/calc.py")  # relevance to the request
    append_memory(project, "always use type hints")
    assert "always use type hints" in read_memory(project)
    assert "# Project memory" in project_context(project)
    assert python_symbols("class A:\n    def m(self, x): pass\n") == ["class A", "  def m(self, x)"]


# ---------------------------------------------------------------------- loop
def test_agent_loop_edits_and_tests(project):
    llm = ScriptedLLM(steps=[
        "Looking at the code.\n" + T("read_file", path="src/calc.py"),
        T("edit_file", path="src/calc.py", old_string="def add(a, b):", new_string="def add(a: int, b: int) -> int:")
        + T("run_tests"),
        "Added type hints to `add`; tests pass.",
    ])
    events = []
    tools = make_tools(project, events=events)
    result = AgentLoop(llm, tools, system="# Role: test", emit=events.append).run("add type hints")
    assert result.stopped == "done" and result.steps == 3 and result.tool_calls == 3
    assert "type hint" in result.text and result.changed == ["src/calc.py"]
    assert tools.last_test and tools.last_test[1] is True
    assert '<result name="read_file">' in llm.calls[1]["messages"][-1]["content"]


def test_agent_loop_stops_repeated_calls_and_max_steps(project):
    llm = ScriptedLLM(steps=[T("grep", pattern="zzz")] * 10)
    result = AgentLoop(llm, make_tools(project), system="# Role: t", max_steps=4).run("search")
    assert result.stopped == "max_steps"
    assert any("repeated the same tool call" in str(m["content"]) for c in llm.calls for m in c["messages"])


# -------------------------------------------------------------------- runner
def test_runner_balanced_reviews_real_diff_and_fixes(project, settings):
    llm = ScriptedLLM(
        steps=[T("write_file", path="src/util.py", content="def double(x):\n    return x * 2\n"),
               "Created util.py",
               T("edit_file", path="src/util.py", old_string="def double(x):", new_string="def double(x: int) -> int:"),
               "Fixed with type hints"],
        gate_verdicts=["REVISE", "APPROVE"])
    orch = Orchestrator(settings, llm=llm)
    runner = AgentRunner(orch, project, PermissionPolicy(mode="auto", root=project))
    events = []
    out = "".join(runner.run("Create a FastAPI endpoint with a users table on Postgres", on_event=events.append))
    reviewer_input = [c for c in llm.calls if c.get("role", "").startswith("Code Reviewer")][0]["messages"][1]["content"]
    assert "+def double(x):" in reviewer_input  # the reviewer sees the real diff
    assert (project / "src" / "util.py").read_text().startswith("def double(x: int) -> int:")
    assert "Fixed with type hints" in out and "✅ Review: approved" in out
    assert "📝 Files changed: src/util.py" in out
    assert any(e["type"] == "info" and e["text"].startswith("review 1") for e in events)
    assert llm.calls[0]["role"].startswith("Architect")


def test_learning_mode_prompt_and_review(project, settings):
    llm = ScriptedLLM(steps=["Your turn: write the body of `add` where you find TODO(you)."])
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(mode="auto", root=project),
                         learn=True)
    "".join(runner.run("/fast explain how add works"))
    system = [c for c in llm.calls if c.get("role") == "agent"][0]["messages"][0]["content"]
    assert "Learning mode" in system and "TODO(you)" in system

    class Board:  # the reviewers: a TODO(you) is the user's exercise, not an issue
        def gate_node(self, payload):
            return {"issues": [{"agent": "reviewer", "severity": "BLOCKER", "text": "TODO(you) not implemented"},
                               {"agent": "reviewer", "severity": "MAJOR", "text": "validation is missing"}]}

    blocking = runner._review(Board(), {}, ["reviewer"], "", make_tools(project), "", 0)
    assert [i["text"] for i in blocking] == ["validation is missing"]


def test_runner_fast_mode_single_agent(project, settings):
    llm = ScriptedLLM(steps=["The add function sums two numbers."])
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(mode="ask", root=project))
    out = "".join(runner.run("what does add do in python?"))
    assert out.startswith("The add function") and [c["role"] for c in llm.calls] == ["agent"]
    system = llm.calls[0]["messages"][0]["content"]
    assert "# Repository map" in system and "src/calc.py" in system


def test_runner_cancel(project, settings):
    import threading
    cancel = threading.Event()
    cancel.set()
    llm = ScriptedLLM(steps=["x"])
    events = []
    out = "".join(AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(root=project)).run(
        "hi", on_event=events.append, cancel=cancel))
    assert out == "" and events[-1]["type"] == "cancelled"
    assert re.search("cancelled", str(events))


def test_agent_nudges_when_model_pastes_code(project):
    llm = ScriptedLLM(steps=[
        "Here is the code:\n```python\ndef sub(a, b):\n    return a - b\n```",
        T("edit_file", path="src/calc.py", old_string="def add(a, b):\n    return a + b",
          new_string="def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b"),
        "Added sub in src/calc.py.",
    ])
    result = AgentLoop(llm, make_tools(project), system="# Role: t").run("add sub")
    assert result.text == "Added sub in src/calc.py." and result.changed == ["src/calc.py"]
    assert "def sub" in (project / "src" / "calc.py").read_text()


def test_agent_applies_code_written_in_the_answer(project):
    full = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    llm = ScriptedLLM(steps=[
        f"Here is src/calc.py:\n```python\n{full}```\nAnd a new file `src/util.py`:\n```python\nX = 1\n```\n"
        "Fragment of test_calc.py:\n```python\nx\n```",
        "Done.",
    ])
    loop = AgentLoop(llm, make_tools(project), system="# Role: t")
    loop.apply_code = True
    result = loop.run("add sub")
    assert (project / "src" / "calc.py").read_text() == full and (project / "src" / "util.py").read_text() == "X = 1\n"
    assert "assert add" in (project / "test_calc.py").read_text()  # a fragment does not overwrite the whole file
    assert result.text == "Done." and sorted(result.changed) == ["src/calc.py", "src/util.py"]


# ------------------------------------------------------------------ ultra-deep
@dataclass
class UltraLLM(ScriptedLLM):
    integrator: list[str] = field(default_factory=list)

    def _answer(self, role, messages):
        if role.startswith("Chief Integrator"):
            return self.integrator.pop(0) if self.integrator else "DECISION: SHIP\n- [MINOR] x: y\nDropped: none"
        if role.startswith("Devil"):
            return "**Verdict**: ADJUST — add input validation"
        return super()._answer(role, messages)


def test_ultra_deep_agent_mode_runs_all_35_agents(project, settings):
    llm = UltraLLM(
        steps=[T("write_file", path="src/util.py", content="def double(x):\n    return x * 2\n"), "Created util.py",
               T("edit_file", path="src/util.py", old_string="def double(x):", new_string="def double(x: int) -> int:"),
               "Fixed", "Docs ok"],
        integrator=["DECISION: FIX\n- [MAJOR] src/util.py: add type hints (from: reviewer)\nDropped: none",
                    "DECISION: SHIP\n- [MINOR] naming\nDropped: none"])
    orch = Orchestrator(settings, llm=llm)
    events = []
    out = "".join(AgentRunner(orch, project, PermissionPolicy(mode="auto", root=project)).run(
        "/ultra-deep create a double function", on_event=events.append))
    roles = {c["role"] for c in llm.calls}
    for role in ("Requirements Analyst", "Architect", "Devil's Advocate", "Test Strategist", "Chief Integrator",
                 "Web Fact-checker", "Dependencies & Supply-chain agent", "Threat Modeling & Privacy reviewer",
                 "Release & Versioning agent", "Documentation", "Output Formatter & Final Delivery",
                 "Mobile Engineer", "Security reviewer"):
        assert any(r.startswith(role) for r in roles), role
    done = [e for e in events if e["type"] == "done"][-1]["summary"]
    assert done.startswith("ultra-deep · 35 agents")
    assert (project / "src" / "util.py").read_text().startswith("def double(x: int) -> int:")
    assert any(e["type"] == "info" and e["text"].startswith("fixes, round 1") for e in events)
    assert any(e["type"] == "agent_skip" and e["agent"] == "research" for e in events)  # offline in tests
    assert "📝 Files changed: src/util.py" in out
    lens = [c for c in llm.calls if c["role"].startswith("Mobile Engineer")][0]
    assert "Quick lens review" in lens["messages"][1]["content"]


def test_ultra_deep_chat_mode(settings):
    llm = UltraLLM()
    orch = Orchestrator(settings, llm=llm)
    out = orch.ask("/ultra-deep create a FastAPI endpoint")
    assert orch.last_run.route.mode == "ultra-deep"
    assert "def add" in out and "Sandbox" in out
    assert len({c["role"] for c in llm.calls}) >= 30


def test_router_ultra(settings, registry):
    from mydevagent.router import Router

    router = Router(settings, registry)
    r = router.route("/ultra-deep mobile app with react native and flutter")
    assert r.mode == "ultra-deep" and "mobile" in r.ultra_relevant
    assert router.route("create a mobile app with react native").mode != "ultra-deep"  # never automatic
    assert "mobile" not in router.route("simple react native app").scores  # extended agents stay out of normal modes
    assert router.route("enterprise production-ready system with a full audit").suggest_ultra


def test_runner_insists_when_no_file_changed(project, settings):
    llm = ScriptedLLM(steps=["Here is how: add the function.",  # no changes
                             T("write_file", path="src/sub.py", content="def sub(a, b):\n    return a - b\n"),
                             "Done."])
    out = "".join(AgentRunner(Orchestrator(settings, llm=llm), project,
                              PermissionPolicy(mode="auto", root=project)).run("/fast add sub in src/sub.py"))
    assert (project / "src" / "sub.py").exists() and "No files changed" not in out


def test_failed_turns_are_not_shown_to_the_model(project, settings):
    # the chat Studio resumed held the "no changes" answers: the model copied them
    llm = ScriptedLLM(steps=["I don't know.",
                             T("write_file", path="src/sub.py", content="x = 1\n"), "Done."])
    history = [{"role": "user", "content": "create src/sub.py"},
               {"role": "assistant", "content": "No change is needed.\n\n---\n⚠️ No files changed: …"}]
    out = "".join(AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(mode="auto", root=project))
                  .run("/fast create src/sub.py", history=history))
    task = [c for c in llm.calls if c.get("role") == "agent"][0]["messages"][1]["content"]
    assert "This earlier attempt failed" in task and "No change is needed" not in task
    retry = [c for c in llm.calls if c.get("role") == "agent"][1]["messages"][-1]["content"]
    assert retry.endswith("The request: create src/sub.py")
    assert (project / "src" / "sub.py").exists() and "No files changed" not in out


def test_footer_warns_when_nothing_changed(project, settings):
    llm = ScriptedLLM(steps=["I do not know how.", "Still nothing."])
    out = "".join(AgentRunner(Orchestrator(settings, llm=llm), project,
                              PermissionPolicy(mode="auto", root=project)).run("/fast add sub in calc"))
    assert "⚠️ No files changed" in out


# ---------------------------------------------------------------------- skill
def write_skills(project, tmp_path, monkeypatch):
    skill = project / ".mydevagent" / "skills" / "changelog"
    (skill / "templates").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: changelog\ndescription: Writes the changelog from the git log.\n---\n"
                                    "# Changelog\nGroup by Added/Fixed. Use templates/base.md.\n")
    (skill / "templates" / "base.md").write_text("## [version]\n")
    user = tmp_path / "state" / "skills"
    user.mkdir(parents=True)
    (user / "changelog.md").write_text("user version, must lose to the project one\n")
    (user / "Commit Style.md").write_text("# Short commit messages in the imperative mood\n")
    extra = tmp_path / "bluagent-skill"
    extra.mkdir()
    (extra / "translate.md").write_text("Translate into English, keeping the code as is.\n")
    monkeypatch.setenv("MYDEVAGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MYDEVAGENT_SKILLS_DIRS", str(extra))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


def test_load_skills_sources_and_reading(project, tmp_path, monkeypatch):
    from mydevagent.skills import load_skills, skills_prompt

    write_skills(project, tmp_path, monkeypatch)
    skills = load_skills(project)
    assert set(skills) == {"changelog", "commit-style", "translate"}
    assert skills["changelog"].source == "project" and skills["translate"].source == "extra"
    assert skills["commit-style"].description == "Short commit messages in the imperative mood"
    body = skills["changelog"].read()
    assert "Group by Added/Fixed" in body and "- templates/base.md" in body and "---" not in body
    assert skills["changelog"].read("templates/base.md") == "## [version]\n"
    assert skills["changelog"].read("../../../.env").startswith("ERROR")  # no escaping the folder
    prompt = skills_prompt(skills)
    assert "- changelog: Writes the changelog from the git log." in prompt and "`skill` tool" in prompt


def test_agent_uses_skill_tool(project, settings, tmp_path, monkeypatch):
    write_skills(project, tmp_path, monkeypatch)
    llm = ScriptedLLM(steps=[T("skill", name="changelog"), "Here is the changelog."])
    events = []
    runner = AgentRunner(Orchestrator(settings, llm=llm), project, PermissionPolicy(mode="ask", root=project))
    out = "".join(runner.run("how do I write the changelog?", on_event=events.append))
    assert out.startswith("Here is the changelog.")
    system = llm.calls[0]["messages"][0]["content"]
    assert "# Skills" in system and "- translate:" in system
    tool_result = str(llm.calls[1]["messages"])
    assert "Group by Added/Fixed" in tool_result  # read without asking permission
    assert any(e["type"] == "info" and e["text"] == "skill changelog" for e in events)


def test_no_skills_no_tool(project):
    assert "skill" not in [s["name"] for s in make_tools(project).specs()]
