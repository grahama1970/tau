FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/tau/.venv

WORKDIR /opt/tau

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY docs ./docs
COPY experiments ./experiments
COPY ui ./ui
COPY docker ./docker

RUN uv sync --frozen

ENV PATH="/opt/tau/.venv/bin:${PATH}" \
    TAU_ARTIFACT_ROOT=/data/artifacts \
    TAU_RECEIPT_DIR=/data/receipts

RUN mkdir -p /data/artifacts /data/receipts

ENTRYPOINT ["tau"]
CMD ["--help"]
