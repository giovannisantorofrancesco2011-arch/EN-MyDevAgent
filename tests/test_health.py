import json

import httpx
import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from mydevagent import health
from mydevagent.llm import FakeLLM
from mydevagent.orchestrator import Orchestrator
from mydevagent.tui.app import TuiApp

detect_hardware = health.detect_hardware


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MYDEVAGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(health, "detect_hardware", lambda: health.Hardware(gpu_gb=8, gpu_name="RTX 4060"))


def fake_models(monkeypatch, models: list[str] | None):
    """The backend's GET /models: the given list, or connection refused if None."""

    def get(url, headers=None, timeout=None):
        if models is None:
            raise httpx.ConnectError("Connection refused")
        return httpx.Response(200, json={"data": [{"id": m} for m in models]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(health.httpx, "get", get)


def test_check_backends_missing_models(settings, monkeypatch):
    settings.profile = "gpu8"
    fake_models(monkeypatch, ["qwen2.5-coder:1.5b", "nomic-embed-text:latest", "llama3.2:3b"])
    status = health.check_backends(settings)
    assert not status.down
    missing = {m.tier: m.model for m in status.missing()}
    assert missing["main"] == "qwen2.5-coder:7b" and "fast" not in missing and "embed" not in missing
    assert not status.ok
    subs = health.substitutes(status)
    assert subs["main"] == "qwen2.5-coder:1.5b"  # coder models take precedence
    assert "vision" not in subs  # no vision model installed


def test_check_backends_down(settings, monkeypatch):
    fake_models(monkeypatch, None)
    status = health.check_backends(settings)
    assert status.down and not status.ok
    assert all(t.installed is None for t in status.tiers)


def test_suggest_substitute_orders():
    installed = {"qwen2.5-coder:14b", "qwen2.5-coder:1.5b", "llama3.1:70b", "nomic-embed-text", "qwen2.5vl:7b"}
    assert health.suggest_substitute("main", installed) == "qwen2.5-coder:14b"
    assert health.suggest_substitute("fast", installed) == "qwen2.5-coder:1.5b"
    assert health.suggest_substitute("embed", installed) == "nomic-embed-text"
    assert health.suggest_substitute("vision", installed) == "qwen2.5vl:7b"
    assert health.suggest_substitute("main", {"nomic-embed-text"}) is None


def test_explain_error_cases(settings):
    import openai

    req = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    not_found = openai.NotFoundError("Error code: 404 - {'error': {'message': \"model 'qwen2.5-coder:7b' not "
                                     "found, try pulling it first\"}}", response=httpx.Response(404, request=req),
                                     body=None)
    title, hint = health.explain_error(not_found, settings)
    assert "qwen2.5-coder:7b is not installed" in title and "/pull qwen2.5-coder:7b" in hint

    title, hint = health.explain_error(openai.APIConnectionError(request=req), settings)
    assert "can't reach the model server" in title and "11434" in title

    title, _ = health.explain_error(openai.APITimeoutError(request=req), settings)
    assert "too slow" in title

    title, hint = health.explain_error(RuntimeError("CUDA out of memory"), settings)
    assert "memory" in title and "-p cpu" in hint

    title, hint = health.explain_error(ValueError("nope"), settings)
    assert title == "ValueError: nope" and "/doctor" in hint


def test_hardware_detection_and_profile():
    smi = "NVIDIA GeForce RTX 3060, 12288\nNVIDIA GeForce GT 1030, 2048\n"
    hw = detect_hardware(run=lambda cmd: smi if cmd[0] == "nvidia-smi" else "")
    assert hw.gpu_gb == 12 and "3060" in hw.gpu_name
    assert health.recommend_profile(hw) == "gpu8"
    assert health.recommend_profile(health.Hardware(gpu_gb=16)) == "gpu16"
    assert health.recommend_profile(health.Hardware(gpu_gb=24)) == "gpu24"
    assert health.recommend_profile(health.Hardware(apple_gb=32)) == "gpu24"
    assert health.recommend_profile(health.Hardware(ram_gb=16)) == "cpu"
    assert "12 GB" in hw.describe()


def test_pull_model_streams_progress():
    lines = [{"status": "pulling manifest"}, {"status": "pulling abc", "total": 100, "completed": 40},
             {"status": "pulling abc", "total": 100, "completed": 100}, {"status": "success"}]

    def handler(request):
        assert request.url.path == "/api/pull" and json.loads(request.content)["model"] == "qwen2.5-coder:0.5b"
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))

    seen = []
    health.pull_model("http://localhost:11434/v1", "qwen2.5-coder:0.5b", lambda *a: seen.append(a),
                      client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen[1] == ("pulling abc", 40, 100) and seen[-1][0] == "success"

    def missing(request):
        return httpx.Response(200, text=json.dumps({"error": "pull model manifest: file does not exist"}))

    with pytest.raises(RuntimeError, match="file does not exist"):
        health.pull_model("http://localhost:11434/v1", "nope", client=httpx.Client(
            transport=httpx.MockTransport(missing)))


def test_save_env_updates_and_appends(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MYDEVAGENT_PROFILE=gpu8\n# MYDEVAGENT_MODEL_MAIN=qwen2.5-coder:7b\n")
    for key in ("MYDEVAGENT_MODEL_MAIN", "MYDEVAGENT_MODEL_FAST"):
        monkeypatch.setenv(key, "")  # save_env sets them: this way they are restored at the end of the test
    health.save_env({"MYDEVAGENT_MODEL_MAIN": "llama3.2:3b", "MYDEVAGENT_MODEL_FAST": "qwen2.5:0.5b"}, env)
    text = env.read_text()
    assert "MYDEVAGENT_MODEL_MAIN=llama3.2:3b" in text and "# MYDEVAGENT_MODEL_MAIN" not in text
    assert text.endswith("MYDEVAGENT_MODEL_FAST=qwen2.5:0.5b\n") and "MYDEVAGENT_PROFILE=gpu8" in text


def make_app(settings, tmp_path, answers):
    console = Console(record=True, width=110, force_terminal=False, color_system=None)
    it = iter(answers)
    with create_pipe_input() as pipe:
        app = TuiApp(Orchestrator(settings, llm=FakeLLM()), console=console, prompt_input=pipe,
                     prompt_output=DummyOutput(), ask=lambda q: next(it), root=tmp_path, background=False)
    return app, console


def test_startup_uses_installed_models(settings, tmp_path, monkeypatch):
    settings.profile = "gpu8"
    fake_models(monkeypatch, ["qwen2.5-coder:1.5b", "nomic-embed-text"])
    saved = {}
    monkeypatch.setattr(health, "save_env", lambda values: saved.update(values) or tmp_path / ".env")
    app, console = make_app(settings, tmp_path, ["2", "y"])
    app.startup_check()
    out = console.export_text()
    assert "Some models are missing" in out and "qwen2.5-coder:7b" in out and "Download them now" in out
    assert settings.resolve_model("main")[0] == "qwen2.5-coder:1.5b" and app.model == "qwen2.5-coder:1.5b"
    assert saved["MYDEVAGENT_MODEL_MAIN"] == "qwen2.5-coder:1.5b"
    assert "recommend" not in out  # the gpu8 profile already fits an 8 GB GPU


def test_startup_backend_down_then_up(settings, tmp_path, monkeypatch):
    settings.profile = "gpu8"
    calls = {"n": 0}

    def get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("Connection refused")
        models = ["qwen2.5-coder:7b", "qwen2.5-coder:1.5b", "nomic-embed-text", "qwen2.5vl:7b"]
        return httpx.Response(200, json={"data": [{"id": m} for m in models]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(health.httpx, "get", get)
    app, console = make_app(settings, tmp_path, [""])  # Enter = retry
    status = app.startup_check()
    out = console.export_text()
    assert "not responding" in out and "ollama" in out.lower()
    assert status.ok and "missing" not in out


def test_startup_pull_and_hardware_hint(settings, tmp_path, monkeypatch):
    settings.profile = "cpu"
    installed = ["qwen2.5-coder:3b", "qwen2.5-coder:1.5b", "nomic-embed-text", "qwen2.5vl:3b"]
    fake_models(monkeypatch, installed)
    pulled = []
    monkeypatch.setattr(health, "pull_model", lambda url, model, cb: (pulled.append(model), cb("success", 0, 0)))
    app, console = make_app(settings, tmp_path, ["1", "3"])
    app.startup_check()
    out = console.export_text()
    assert pulled == ["qwen2.5-coder:7b"] and "Downloaded qwen2.5-coder:7b" in out
    assert "I recommend the gpu8 profile" in out and "RTX 4060" in out
    console.export_text(clear=True)
    app.startup_check()  # the hardware hint appears only once
    assert "recommend" not in console.export_text()


def test_tui_error_is_explained(settings, tmp_path):
    class Broken(FakeLLM):
        def complete(self, *a, **k):
            raise RuntimeError("model 'qwen2.5-coder:7b' not found (404)")

        def stream(self, *a, **k):
            raise RuntimeError("model 'qwen2.5-coder:7b' not found (404)")

    console = Console(record=True, width=110, force_terminal=False, color_system=None)
    with create_pipe_input() as pipe:
        app = TuiApp(Orchestrator(settings, llm=Broken()), console=console, prompt_input=pipe,
                     prompt_output=DummyOutput(), ask=lambda q: "1", root=tmp_path, background=False)
        app.agent_mode = False
        app.run_turn("/fast hello", {})
    out = console.export_text()
    assert "The model qwen2.5-coder:7b is not installed" in out and "/pull qwen2.5-coder:7b" in out
