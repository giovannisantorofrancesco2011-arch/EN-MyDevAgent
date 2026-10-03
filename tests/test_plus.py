import threading

import pytest

from mydevagent import plus
from mydevagent.llm import FakeLLM


def test_commands_become_agent_tasks():
    assert "git_diff" in plus.expand("/review-changes", "")
    assert "`pytest -x`" in plus.expand("/debug", "pytest -x")
    assert "`app.py`" in plus.expand("/test", "app.py")
    assert plus.expand("/other", "") is None
    with pytest.raises(ValueError):
        plus.expand("/debug", "")


def test_auto_memory_saves_at_most_two_facts(tmp_path):
    llm = FakeLLM(responses={"unknown": "- tests run with `pytest -q`\n- use tabs\n- third\nhello"})
    facts = plus.remember(llm, tmp_path, "how do I run the tests?", "With pytest -q")
    assert facts == ["tests run with `pytest -q`", "use tabs"]
    assert "- use tabs" in (tmp_path / "MYDEVAGENT.md").read_text(encoding="utf-8")
    assert plus.remember(FakeLLM(responses={"unknown": "NONE"}), tmp_path, "hi", "hi!") == []


def test_background_job_reports_back(monkeypatch, tmp_path):
    class Runner:
        def __init__(self, orch, root, policy, approver):
            assert policy.mode == "auto-edit"
            assert approver(plus.ApprovalRequest("bash", {}, "rm -rf /", dangerous=True))[0] == "no"

        def run(self, task):
            yield f"done: {task}"

    monkeypatch.setattr(plus, "AgentRunner", Runner)
    done = threading.Event()
    jobs = plus.Background(None, tmp_path, lambda job: done.set())
    job = jobs.start("add the README")
    assert done.wait(5)
    assert (job.status, job.answer) == ("done", "done: add the README")
