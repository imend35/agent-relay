FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

# Install dependencies in a separate layer so application-only changes are
# cheap to rebuild.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY database.py dashboard.py dashboard.html errors.py main.py schemas.py storage.py worker.py ./

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
