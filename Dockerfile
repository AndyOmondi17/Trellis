# ---------------------------------------------------------------------------
# Stage 1: Builder (compile wheels & install dependencies into isolated venv)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[graph]"

# ---------------------------------------------------------------------------
# Stage 2: Minimal Non-Root Production Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    DB_BACKEND=relational

RUN groupadd --gid 10001 trellis && \
    useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash trellis

COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/
COPY data/clean_spec/ ./data/clean_spec/
COPY schemas/ ./schemas/
COPY alembic.ini ./
COPY migrations/ ./migrations/

RUN chown -R trellis:trellis /app

USER trellis

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "trellis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
