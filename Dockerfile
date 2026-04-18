# Build & runtime image for sber26-bot (Railway target).
# Uses the official uv image with Python 3.12 pre-installed.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Install dependencies first for better layer caching.
# No BuildKit cache mounts — Railway's builder rejects them without an id,
# and they're only a speed-up anyway.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

# Copy project sources.
COPY . .

# Install the project itself (so `uv run` resolves the local package).
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Long-polling — no HTTP port to expose.
# Default command comes from railway.toml; keep CMD as a sane fallback.
CMD ["uv", "run", "python", "-m", "src.bot.main"]
