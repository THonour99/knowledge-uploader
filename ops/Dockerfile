ARG POSTGRES_IMAGE=postgres:16-bookworm
ARG MC_IMAGE=minio/mc:RELEASE.2024-04-18T16-45-29Z

FROM --platform=$BUILDPLATFORM ${POSTGRES_IMAGE} AS source
WORKDIR /source
COPY ops/backup_restore.py ./

FROM --platform=$TARGETPLATFORM ${MC_IMAGE} AS target-mc

FROM --platform=$TARGETPLATFORM ${POSTGRES_IMAGE} AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=target-mc /usr/bin/mc /usr/local/bin/mc
COPY --from=source /source/backup_restore.py /app/backup_restore.py

ENTRYPOINT ["python3", "/app/backup_restore.py"]
