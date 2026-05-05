# Daedalus Postgres backup sidecar.
#
# Runs `pg_dump` on a cron schedule and pushes the gzipped dump to MinIO.
# Prunes anything older than $RETENTION_DAYS on every successful upload.
# The container stays alive between cron firings.

FROM postgres:16-alpine

RUN apk add --no-cache \
        bash \
        curl \
        ca-certificates \
        coreutils \
        findutils \
        python3 \
        tzdata

# MinIO mc client — pinned to a specific release.
ARG MC_VERSION=RELEASE.2024-11-21T17-21-54Z
RUN ARCH="$(uname -m)" && \
    case "$ARCH" in \
      x86_64)  MCARCH=amd64 ;; \
      aarch64) MCARCH=arm64 ;; \
      *) echo "unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    curl -fsSL -o /usr/local/bin/mc \
      "https://dl.min.io/client/mc/release/linux-${MCARCH}/archive/mc.${MC_VERSION}" && \
    chmod +x /usr/local/bin/mc

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY backup.sh     /usr/local/bin/pg-backup
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/pg-backup

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
