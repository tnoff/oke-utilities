# Own the secret-age tracker's dependency set: kubernetes, oci, requests,
# dappertable. No Trivy binary, no OpenTelemetry SDK/exporters -- this
# CronJob never scans images and has no OTLP setup at all (plain stdout
# logging only). Build context is the repo root so this stage can also
# COPY packages/core.
FROM python:3.15-slim AS py-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/core/src packages/core/src
COPY packages/secret_age/pyproject.toml packages/secret_age/pyproject.toml
COPY packages/secret_age/src packages/secret_age/src
RUN pip install --no-cache-dir --prefix=/install ./packages/core ./packages/secret_age


FROM python:3.15-slim

RUN apt-get update && \
    apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

COPY --from=py-builder /install /usr/local

RUN useradd -m -u 1000 scanner
USER scanner

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "secret_age"]
