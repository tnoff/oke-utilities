# Build context is the repo root, like the other images.
#
# The build stage also runs vet and the tests, so CI's image build (build-rpst-sidecar) is the Go
# test gate: tox covers only the Python packages.
FROM golang:1.27.4-bookworm AS go-builder

WORKDIR /build
COPY packages/rpst_sidecar/ ./
RUN go vet ./... && go test -count=1 ./...
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.version=$(cat VERSION)" -o /out/rpst-sidecar .


# Static binary, CA bundle, no shell, no package manager.
FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=go-builder /out/ /usr/local/bin/

# The Python image's `rpst` user was uid/gid 1000; keep it so file ownership on the shared
# volume does not change under existing pods.
USER 1000:1000
ENV PATH=/usr/local/bin:/usr/bin:/bin

LABEL org.opencontainers.image.source="https://github.com/tnoff/oke-utilities" \
      org.opencontainers.image.description="Trades a projected ServiceAccount token for an OCI resource principal session token on a loop (tnoff/terraform#191)"

# CMD, not ENTRYPOINT, as before: a pod spec's `args:` replaces the whole command line, and
# `args: [rpst-sidecar, --once]` names the binary itself.
CMD ["rpst-sidecar"]
