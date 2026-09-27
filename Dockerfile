ARG PYTHON_IMAGE=python:3.13.7-slim-bookworm
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_NO_PROGRESS=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.12.19
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY agent_lab ./agent_lab
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:${PATH}"
COPY config ./config
COPY demo/tasks ./demo/tasks
RUN useradd --uid 10001 --create-home lab && mkdir -p /app/workspaces && chown -R lab:lab /app/workspaces
USER lab
CMD ["uvicorn", "agent_lab.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM base AS proxy
USER root
RUN uv sync --frozen --no-dev --group proxy
USER lab
CMD ["litellm", "--config", "/app/config/litellm.yaml", "--port", "4000", "--num_workers", "1"]
