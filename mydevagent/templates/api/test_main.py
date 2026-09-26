from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_home():
    assert client.get("/").status_code == 200


def test_add_and_complete_todo():
    todo = client.post("/todos", json={"text": "study"}).json()
    assert todo["done"] is False
    assert client.post(f"/todos/{todo['id']}/done").json()["done"] is True
    assert client.post("/todos/999/done").status_code == 404
