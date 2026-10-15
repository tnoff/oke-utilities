# The sidecar's whole dependency set is `cryptography`: it talks to the identity domain
# with urllib. No OCI SDK, no kubernetes client, no oke-scanner-core.
# Build context is the repo root, like the other images.
FROM python:3.15-slim AS py-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY packages/rpst_sidecar/pyproject.toml packages/rpst_sidecar/pyproject.toml
COPY packages/rpst_sidecar/src packages/rpst_sidecar/src
RUN pip install --no-cache-dir --prefix=/install ./packages/rpst_sidecar


FROM python:3.15-slim

RUN apt-get update && \
    apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

COPY --from=py-builder /install /usr/local

RUN useradd -m -u 1000 rpst
USER rpst

ENV PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/tnoff/oke-utilities" \
      org.opencontainers.image.description="Trades a projected ServiceAccount token for an OCI resource principal session token on a loop (tnoff/terraform#191)"

CMD ["python", "-m", "rpst_sidecar"]
