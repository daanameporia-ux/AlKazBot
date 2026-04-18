# Build & runtime image for sber26-bot (Railway target).
# Uses the official uv image with Python 3.12 pre-installed.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first for better layer caching. No BuildKit cache
# mounts — Railway's builder rejects them without an id.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

# Copy project sources.
COPY . .

# Install the project itself.
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Put .venv on PATH so `alembic`, `python`, etc. are resolved directly —
# no `uv run` at runtime (which triggered a fresh sync on every start).
ENV PATH="/app/.venv/bin:$PATH"

# Long-polling — no HTTP port to expose.
CMD ["python", "-m", "src.bot.main"]
