# Official historical image registries are no longer reliably public.
# Build the pinned upstream release through the Go module checksum database.
# Source: https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z
FROM golang:1.24.8-bookworm AS builder
ENV CGO_ENABLED=0 GOBIN=/out
RUN go install -trimpath github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/* && useradd -u 10001 -m minio && mkdir /data && chown minio:minio /data
COPY --from=builder /out/minio /usr/local/bin/minio
USER minio
EXPOSE 9000
VOLUME /data
HEALTHCHECK --interval=5s --timeout=3s --retries=30 CMD curl --fail http://127.0.0.1:9000/minio/health/ready || exit 1
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
