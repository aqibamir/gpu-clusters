FROM python:3.12-slim

WORKDIR /app

# The orchestrator (Zone 2) is PII-blind coordination only — no llama.cpp, no spaCy.
COPY pyproject.toml .
RUN pip install --no-cache-dir uv && \
    uv pip install --system \
        "fastapi>=0.111.0" \
        "uvicorn[standard]>=0.30.0" \
        "pydantic>=2.0.0" \
        "cryptography>=42.0.0"

COPY cgn/ cgn/

# Railway injects PORT at runtime; fall back to 8000 locally.
CMD ["sh", "-c", "uvicorn --factory cgn.orchestrator_app:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
