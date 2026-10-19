# The OCI SDK is the whole dependency set: no kubernetes client, no oke-scanner-core.
# Build context is the repo root, like the other images.
FROM python:3.15-slim AS py-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY packages/audit_watch/pyproject.toml packages/audit_watch/pyproject.toml
COPY packages/audit_watch/src packages/audit_watch/src
RUN pip install --no-cache-dir --prefix=/install ./packages/audit_watch


FROM python:3.15-slim

RUN apt-get update && \
    apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

COPY --from=py-builder /install /usr/local

RUN useradd -m -u 1000 watch
USER watch

ENV PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/tnoff/oke-utilities" \
      org.opencontainers.image.description="Summarises what OCI resource principals did in the Audit log, flagging denied calls and unexpected writes (tnoff/terraform#191)"

CMD ["python", "-m", "audit_watch"]
