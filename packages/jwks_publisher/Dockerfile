# The JWKS publisher's whole dependency set is the OCI SDK: it reads the key set
# from the API server with the standard library and writes one Object Storage
# object. No kubernetes client, no oke-scanner-core, no OpenTelemetry, no Trivy.
# Build context is the repo root, like the other images.
FROM python:3.15-slim AS py-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY packages/jwks_publisher/pyproject.toml packages/jwks_publisher/pyproject.toml
COPY packages/jwks_publisher/src packages/jwks_publisher/src
RUN pip install --no-cache-dir --prefix=/install ./packages/jwks_publisher


FROM python:3.15-slim

RUN apt-get update && \
    apt-get -y upgrade && \
    rm -rf /var/lib/apt/lists/*

COPY --from=py-builder /install /usr/local

# The home directory matters: the bot's OCI config (terraform output
# oidc_jwks_publisher_oci_config) points key_file at /home/publisher/.oci/api_key.pem.
RUN useradd -m -u 1000 publisher
USER publisher

ENV PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/tnoff/oke-utilities" \
      org.opencontainers.image.description="Mirrors the cluster's ServiceAccount public key set into the hosted OCI bucket (tnoff/terraform#191)"

CMD ["python", "-m", "jwks_publisher"]
