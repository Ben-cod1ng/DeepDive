# Locked runtime dependencies; build without credentials.
FROM ghcr.io/astral-sh/uv:0.9.0 AS uv
FROM python:3.13-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

FROM python:3.13-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DEEPDIVE_DATA_DIR=/data MPLCONFIGDIR=/tmp/matplotlib \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken HOME=/tmp \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
RUN groupadd --gid 10001 deepdive && useradd --uid 10001 --gid deepdive --no-create-home deepdive \
    && mkdir /data && chown deepdive:deepdive /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/tiktoken /opt/tiktoken
COPY deepdive ./deepdive
COPY streamlit_app.py ./
COPY uv.lock ./
COPY evaluation/claims.v1.json evaluation/questions.v1.json ./evaluation/
COPY evaluation/documents.v1.json ./evaluation/
USER 10001:10001
EXPOSE 8000 8501
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "deepdive.ops", "health"]
CMD ["python", "-m", "uvicorn", "deepdive.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
