FROM python:3.15-slim AS trivy-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

ARG TRIVY_VERSION=v0.70.0
RUN curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | \
    sh -s -- -b /usr/local/bin ${TRIVY_VERSION}


# No build-essential needed here -- oci (and its crc32c transitive dep, the
# reason build-essential existed in this stage) moved out with
# packages/ocir_cleanup, and scan no longer depends on the OCI SDK at all.
# Build context is the repo root so this stage can also COPY packages/core.
FROM python:3.15-slim AS py-builder

WORKDIR /build
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/core/src packages/core/src
COPY packages/scan/pyproject.toml packages/scan/pyproject.toml
COPY packages/scan/src packages/scan/src
RUN pip install --no-cache-dir --prefix=/install ./packages/core ./packages/scan


FROM python:3.15-slim

# Apply security upgrades only; no build tools needed in the final image.
RUN apt-get update && \
    apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

# Copy the trivy binary from the builder stage
COPY --from=trivy-builder /usr/local/bin/trivy /usr/local/bin/trivy

# Copy Python deps installed in the py-builder stage
COPY --from=py-builder /install /usr/local

WORKDIR /app

# Run as non-root user
RUN useradd -m -u 1000 scanner && \
    mkdir -p /home/scanner/.cache/trivy && \
    chown -R scanner:scanner /home/scanner/.cache

USER scanner

ENV PYTHONUNBUFFERED=1
ENV TRIVY_CACHE_DIR=/home/scanner/.cache/trivy

CMD ["python", "-m", "scan"]
