# MyDevAgent with Aider, Open WebUI and other OpenAI-compatible clients

## Aider (terminal pair programming, edits files and commits)
```bash
pip install aider-chat
export OPENAI_API_BASE=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local            # or MYDEVAGENT_API_KEY
aider --model openai/mydevagent-fast   # quick edits
aider --model openai/mydevagent        # full team for complex tasks
```
Aider asks the model to answer in specific edit formats: the team follows them in fast
mode (a single call with Aider's instructions). If you prefer, use Ollama directly:
`aider --model ollama_chat/mydevagent`.

## Open WebUI (ChatGPT-style web interface)
Settings → Connections → OpenAI API → URL `http://host.docker.internal:8000/v1`, key `local`.

## Python (OpenAI SDK)
```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="local")
stream = client.chat.completions.create(
    model="mydevagent", stream=True,
    messages=[{"role": "user", "content": "Write a token-bucket rate limiter in Go with tests"}],
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

## Python (library, no server)
```python
from mydevagent import Orchestrator
print(Orchestrator().ask("/deep FastAPI API for file uploads to S3 with validation and tests"))
```
