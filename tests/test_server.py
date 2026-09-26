import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mydevagent.server import create_app, split_messages  # noqa: E402


@pytest.fixture
def client(orchestrator):
    return TestClient(create_app(orchestrator))


def test_models(client):
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert ids == ["mydevagent", "mydevagent-fast", "mydevagent-balanced", "mydevagent-deep"]


def test_chat_non_stream(client):
    resp = client.post("/v1/chat/completions", json={
        "model": "mydevagent-fast", "messages": [{"role": "user", "content": "sum in python"}]})
    body = resp.json()
    assert resp.status_code == 200
    assert "def add" in body["choices"][0]["message"]["content"]


def test_chat_stream_with_progress(client):
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "mydevagent-balanced", "stream": True,
        "messages": [{"role": "system", "content": "ignored"},
                     {"role": "user", "content": "FastAPI API with Postgres"}]}) as resp:
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    text = "".join(json.loads(line[6:])["choices"][0]["delta"].get("content", "") for line in lines[:-1])
    assert "⟢ MyDevAgent" in text and "def add" in text


def test_api_key(orchestrator, monkeypatch):
    monkeypatch.setenv("MYDEVAGENT_API_KEY", "s3cret")
    client = TestClient(create_app(orchestrator))
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_split_messages_multimodal():
    text, images, history = split_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"type": "text", "text": "make this UI"},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]},
    ])
    assert text == "make this UI" and images == ["data:image/png;base64,AA"] and len(history) == 2
