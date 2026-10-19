FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/
WORKDIR /app
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Dependency layer: only manifests, so source edits don't bust the cache.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/seedgen/pyproject.toml packages/seedgen/pyproject.toml
COPY apps/mock_tms_api/pyproject.toml apps/mock_tms_api/pyproject.toml
COPY apps/mcp_server/pyproject.toml apps/mcp_server/pyproject.toml
RUN uv sync --frozen --no-dev --all-packages --no-install-workspace

# Source layer (workspace members install editable, so /app keeps the tree).
COPY . .
RUN uv sync --frozen --no-dev --all-packages

ENV PATH=/app/.venv/bin:$PATH
