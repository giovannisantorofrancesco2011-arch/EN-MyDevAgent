from mydevagent.graph import collect_files
from mydevagent.llm import FakeLLM
from mydevagent.orchestrator import Orchestrator


def test_fast_mode_is_a_single_llm_call(orchestrator, fake_llm):
    events = []
    out = orchestrator.ask("How do I reverse a list in Python?", on_event=events.append)
    assert "def add" in out
    assert len(fake_llm.calls) == 1
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "Language & Framework" in system and "Output structure" in system  # fused formatter
    assert events[0]["type"] == "route" and events[-1]["type"] == "done"


def test_balanced_pipeline_runs_team_and_sandbox(orchestrator, fake_llm):
    orchestrator.ask("Create a FastAPI endpoint that saves users to Postgres with SQLAlchemy")
    roles = [c["role"] for c in fake_llm.calls]
    assert roles[0].startswith("Architect")
    assert any("Backend" in r for r in roles) and any("Database" in r for r in roles)
    assert any("Debugging" in r for r in roles) and any("Reviewer" in r for r in roles)
    assert roles[-1].startswith("Output Formatter")
    assert orchestrator.last_run.test_report.startswith("PASS")


def test_review_loop_revises_then_stops(settings):
    llm = FakeLLM(gate_verdicts=["REVISE", "REVISE", "REVISE"])
    orch = Orchestrator(settings, llm=llm)
    events = []
    orch.ask("Create a FastAPI endpoint with a users table on Postgres", on_event=events.append)
    revisions = [e for e in events if e["type"] == "info" and e["text"].startswith("revision round")]
    assert len(revisions) == settings.mode("balanced").max_review_rounds == 1
    formatter_prompt = llm.calls[-1]["messages"][1]["content"]
    assert "[BLOCKER]" in formatter_prompt  # unresolved issues reach the formatter


def test_deep_mode_runs_all_gates_and_docs(orchestrator, fake_llm):
    orchestrator.ask("/deep build a todo app with react and fastapi")
    roles = " | ".join(c["role"] for c in fake_llm.calls)
    for name in ("Security", "Performance", "Edge Cases", "Reviewer", "Documentation"):
        assert name in roles


def test_research_offline_is_graceful(orchestrator, fake_llm):
    orchestrator.ask("/balanced what is the latest version of Next.js? @web")
    assert not any("Research" in c["role"] for c in fake_llm.calls)  # offline: no wasted call
    architect_input = fake_llm.calls[0]["messages"][1]["content"]
    assert "OFFLINE" in architect_input


def test_failing_self_check_creates_blocker(settings):
    llm = FakeLLM(responses={"Debugging": "```python run\nassert 1 == 2\n```"})
    orch = Orchestrator(settings, llm=llm)
    orch.ask("Create a FastAPI endpoint with a users table on Postgres")
    assert orch.last_run.test_report.startswith("FAIL")


def test_agents_only_receive_their_sections(orchestrator, fake_llm):
    orchestrator.ask("Create a FastAPI endpoint with a users table on Postgres", history=[
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "⟢ MyDevAgent · x\nhello!"}])
    security_like = [c for c in fake_llm.calls if c["role"].startswith("Code Reviewer")][0]
    content = security_like["messages"][1]["content"]
    assert "## Conversation so far" not in content  # the reviewer does not read the history
    architect = fake_llm.calls[0]["messages"][1]["content"]
    assert "assistant: hello!" in architect and "⟢" not in architect


def test_collect_files(registry):
    files = collect_files({"backend": "```python file=a.py\nx=1\n```", "database": "```sql file=a.py\ny\n```"},
                          registry)
    assert files == {"a.py": "y\n"}


def test_collect_files_fallback_names(registry):
    files = collect_files({"language": "```python\ndef f():\n    return 1\n```\n```python\nX = 2\n```",
                           "debug_test": "```python\nfrom main import f\ndef test_f():\n    assert f() == 1\n```"},
                          registry)
    assert set(files) == {"main.py", "main_2.py", "test_main.py"}


def test_fallback_runner_executes_plain_tests(settings):
    llm = FakeLLM(responses={
        "Language": "```python\ndef f():\n    return 1\n```",
        "Debugging": "```python\nfrom main import f\n\ndef test_f():\n    assert f() == 2\n```",
    })
    orch = Orchestrator(settings, llm=llm)
    orch.ask("/balanced write a python function f")
    report = orch.last_run.test_report
    assert report.startswith("FAIL") and "1 tests run, 1 failed" in report


def test_sandbox_footer_is_deterministic(orchestrator):
    out = orchestrator.ask("Create a FastAPI endpoint that saves users to Postgres with SQLAlchemy")
    assert out.rstrip().endswith("✅ Sandbox: PASS [local]")
    fast = orchestrator.ask("/fast sum in python")
    assert "Sandbox" not in fast
