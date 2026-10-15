# Own the cleanup sweep's dependency set: kubernetes (via oke-scanner-core),
# oci, requests, dappertable, the OpenTelemetry SDK/exporters (the
# ocir-cleanup-cronjob CronJob genuinely uses OTLP logs+metrics, unlike
# secret-age-tracker). No Trivy binary at all -- this image never scans an
# image. Build context is the repo root so this stage can also COPY
# packages/core.
FROM python:3.15-slim AS py-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/core/src packages/core/src
COPY packages/ocir_cleanup/pyproject.toml packages/ocir_cleanup/pyproject.toml
COPY packages/ocir_cleanup/src packages/ocir_cleanup/src
RUN pip install --no-cache-dir --prefix=/install ./packages/core ./packages/ocir_cleanup


FROM python:3.15-slim

RUN apt-get update && \
    apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

COPY --from=py-builder /install /usr/local

RUN useradd -m -u 1000 scanner
USER scanner

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "ocir_cleanup"]
