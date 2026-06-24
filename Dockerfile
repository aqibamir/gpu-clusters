FROM python:3.12-slim

WORKDIR /app

# Install only what the orchestrator needs (no llama.cpp, no spaCy)
COPY pyproject.toml .
RUN pip install --no-cache-dir uv && \
    uv pip install --system \
        "fastapi>=0.111.0" \
        "uvicorn[standard]>=0.30.0" \
        "httpx>=0.27.0" \
        "psutil>=6.0.0" \
        "cryptography>=42.0.0"

COPY orchestrator/ orchestrator/
COPY anonymizer/ anonymizer/
COPY frontend/ frontend/

# Railway injects PORT at runtime; fall back to 8000 locally
CMD ["sh", "-c", "uvicorn orchestrator.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
