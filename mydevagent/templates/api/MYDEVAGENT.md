# My API

Web API in Python with FastAPI. The data lives in memory (it resets on restart).

## Commands
- install: `python -m pip install -r requirements.txt`
- run: `python -m uvicorn main:app --reload` (interactive docs at http://127.0.0.1:8000/docs)
- test: `python -m pytest -q`

## Conventions
- each endpoint is a function with `@app.get` / `@app.post`, and the data types are Pydantic classes
- for every new endpoint, add a test in test_main.py
