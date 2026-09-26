# MyDevAgent server (OpenAI-compatible) — lightweight image, the model runs in Ollama/vLLM
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MYDEVAGENT_HOME=/app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY mydevagent ./mydevagent
COPY config ./config
COPY prompts ./prompts
RUN pip install --no-cache-dir -e ".[server,search]" \
 && useradd --create-home --uid 10001 agent
USER agent
EXPOSE 8000
CMD ["mydevagent", "serve", "--host", "0.0.0.0", "--port", "8000"]
