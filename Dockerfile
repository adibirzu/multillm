# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: Apache-2.0

# ─── Stage 1: builder ─────────────────────────────────────────────────────────
# Compiles wheels into a throwaway prefix; the runtime stage copies only the
# resulting site-packages tree. Build tooling (gcc, git) never reaches runtime.
FROM python:3.12-slim AS builder

ARG PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Copy pyproject/README first so the dependency layer caches across source-only
# changes. Source files come in a second COPY so editing them does not bust
# the pip-install layer.
COPY pyproject.toml README.md ./
COPY multillm/ ./multillm/

RUN pip install --prefix=/install .

# Compile-only smoke import: fail the build if the entry points cannot be
# resolved against the installed prefix.
RUN PYTHONPATH=/install/lib/python3.12/site-packages \
    python -c "import multillm.cli; import multillm.gateway; print('OK')"


# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ARG OWNER=multillm
ARG REPO=multillm

LABEL org.opencontainers.image.title=multillm \
      org.opencontainers.image.description="Open-source multi-tenant LLM gateway" \
      org.opencontainers.image.source=https://github.com/${OWNER}/${REPO} \
      org.opencontainers.image.licenses=Apache-2.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MULTILLM_HOME=/data \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8080

# Runtime needs only:
#   - curl   : HEALTHCHECK probe against /health
#   - tini   : PID 1 init that forwards SIGTERM cleanly so SQLite WAL flushes
# No compiler, no git — multi-stage discards builder's build-essential.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl \
        tini \
 && rm -rf /var/lib/apt/lists/*

# Dedicated non-root identity. Using uid 10001 keeps the process well above the
# system-account range and predictable for volume-permission tooling.
RUN groupadd -g 10001 multillm \
 && useradd -u 10001 -g multillm -m -s /sbin/nologin multillm

COPY --from=builder /install /usr/local

RUN mkdir -p /data && chown -R multillm:multillm /data

VOLUME ["/data"]

USER multillm

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "multillm migrate up && multillm serve"]
