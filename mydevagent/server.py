"""OpenAI-compatible server: exposes the team as the `mydevagent` model for IDEs and external tools.

    POST /v1/chat/completions   (stream and non-stream)
    GET  /v1/models             mydevagent | mydevagent-fast | mydevagent-balanced | mydevagent-deep
    GET  /health
"""


import hmac
import json
import os
import queue
import threading
import time
import uuid
from typing import Any

from .health import explain_error
from .orchestrator import Orchestrator
from .state import PROGRESS_MARKER

MODELS = {
    "mydevagent": None,
    "mydevagent-fast": "fast",
    "mydevagent-balanced": "balanced",
    "mydevagent-deep": "deep",
}


def split_messages(messages: list[dict[str, Any]]) -> tuple[str, list[str], list[dict[str, Any]]]:
    """→ (text of the last user request, images, previous history without system messages)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    if last_user < 0:
        return "", [], []
    content = messages[last_user].get("content") or ""
    images: list[str] = []
    if isinstance(content, list):
        texts = []
        for part in content:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                url = part.get("image_url", {})
                images.append(url.get("url", "") if isinstance(url, dict) else str(url))
        content = "\n".join(texts)
    history = [m for m in messages[:last_user] if m.get("role") in ("user", "assistant")]
    return content, [i for i in images if i], history


def progress_line(event: dict[str, Any]) -> str | None:
    if event["type"] == "route":
        if event["mode"] == "fast":
            return None
        return f"{PROGRESS_MARKER} · {event['mode']} · {' → '.join(event['agents'])}  \n"
    if event["type"] == "agent_end" and event.get("agent") != "final":
        status = "✗" if event.get("error") else "✓"
        return f"{PROGRESS_MARKER} · {event.get('name', event['agent'])} {status} {event['ms'] / 1000:.1f}s  \n"
    if event["type"] == "info":
        return f"{PROGRESS_MARKER} · {event['text']}  \n"
    return None


def create_app(orchestrator: Orchestrator | None = None):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    orch = orchestrator or Orchestrator()
    app = FastAPI(title="MyDevAgent", version="1.0.0")
    api_key = os.environ.get("MYDEVAGENT_API_KEY", "")

    def check_auth(request: Request) -> None:
        if not api_key:
            return
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(token.encode(), api_key.encode()):
            raise HTTPException(status_code=401, detail="invalid API key")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "profile": orch.settings.profile, "agents": len(orch.registry)}

    @app.get("/v1/models")
    def models(request: Request) -> dict[str, Any]:
        check_auth(request)
        now = int(time.time())
        return {"object": "list",
                "data": [{"id": m, "object": "model", "created": now, "owned_by": "mydevagent"} for m in MODELS]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        check_auth(request)
        body = await request.json()
        model = body.get("model", "mydevagent")
        mode = MODELS.get(model)
        text, images, history = split_messages(body.get("messages", []))
        if not text.strip():
            raise HTTPException(status_code=400, detail="no user message")
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        show_progress = orch.settings.server.show_progress and mode != "fast"

        def worker(out: queue.Queue) -> None:
            def on_event(event: dict[str, Any]) -> None:
                if show_progress:
                    line = progress_line(event)
                    if line:
                        out.put(("text", line))
                if event["type"] == "agent_start" and event["agent"] == "formatter" and show_progress:
                    out.put(("text", "\n"))

            try:
                for chunk in orch.run(text, history=history, images=images, mode=mode, on_event=on_event):
                    out.put(("text", chunk))
            except Exception as exc:
                title, hint = explain_error(exc, orch.settings)
                out.put(("error", f"{title} ({hint})"))
            finally:
                out.put(("done", None))

        def produce():
            q: queue.Queue = queue.Queue()
            threading.Thread(target=worker, args=(q,), daemon=True).start()
            while True:
                kind, value = q.get()
                if kind == "done":
                    return
                if kind == "error":
                    yield f"\n\n**MyDevAgent error:** {value}\n"
                    return
                yield value

        def chunk_json(delta: dict[str, Any], finish: str | None = None) -> str:
            payload = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": model,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        if body.get("stream"):
            def sse():
                yield chunk_json({"role": "assistant", "content": ""})
                for piece in produce():
                    yield chunk_json({"content": piece})
                yield chunk_json({}, "stop")
                yield "data: [DONE]\n\n"

            return StreamingResponse(sse(), media_type="text/event-stream")

        content = "".join(produce())
        prompt_chars = sum(len(str(m.get("content", ""))) for m in body.get("messages", []))
        return JSONResponse({
            "id": completion_id, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_chars // 4, "completion_tokens": len(content) // 4,
                      "total_tokens": (prompt_chars + len(content)) // 4},
        })

    return app


def serve(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    orch = Orchestrator()
    uvicorn.run(create_app(orch), host=host or orch.settings.server.host, port=port or orch.settings.server.port)
