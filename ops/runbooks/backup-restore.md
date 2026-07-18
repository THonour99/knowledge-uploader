# Backup and restore runbook

This runbook covers validated PostgreSQL and MinIO backups. A backup is not reported as
successful until its database dump has been restored into an isolated verification database,
its object mirror has been hashed, and the manifest has passed the secret-leak guard.
The one-shot container requires a dedicated short-lived MinIO DR operator through
`DR_MINIO_ACCESS_KEY` and `DR_MINIO_SECRET_KEY`. The account must be distinct from both
the application data-plane identity and the MinIO root identity. The drill needs source-bucket
read/list plus create/read/write/delete for its isolated `restore-*` target, after which the
operator is revoked. Compose and runtime checks prove only that the configured identities are
different, the alias declares the same DR operator, and protected traffic uses the mounted CA;
they do not prove the server-side policy is least privilege. Only a real isolated drill can prove
the required operations work, and only explicit denied-operation evidence outside the approved
source and restore scope can support a least-privilege claim. Compose fails closed when either
credential is absent; secret-manager values must be URL-safe because MinIO's `MC_HOST_source`
alias is a URL. Never place either value in phase reports, exceptions, or command output.


## Create a validated backup

Start the normal PostgreSQL and MinIO services, then run the one-shot operations container:

```bash
docker compose up -d postgres minio
docker compose -f docker-compose.yml -f docker-compose.ops.yml --profile ops run --rm \
  backup-restore backup
```

This two-file HTTP form is development-only and requires `APP_ENV=development`.

The backup is written to the `backups` volume only after validation. The manifest records table
row counts (without row-content digests), the Alembic revision, object key/size/ETag/SHA-256 metadata, and only
the key/secret-presence flags for runtime configuration. It never records a configuration value,
credential, token, or connection string.

This command is a logical full-backup validator, not a complete disaster-recovery gate. Its dump
and object mirror are sequential and the manifest records
`uncoordinated_full_dump_then_object_mirror`; it therefore cannot prove a paired database/object
recovery point. It also does not, by itself, prove continuous WAL/PITR, encryption, off-site
retention, MinIO versioning/replication, or recoverability with a separately controlled key.

## Run an isolated restore drill

List the `backups` volume to choose an immutable backup directory, then use names reserved for
restore validation:

```bash
docker compose -f docker-compose.yml -f docker-compose.ops.yml --profile ops run --rm \
  backup-restore restore \
  --backup-dir /backups/<backup-id> \
  --target-environment staging \
  --target-database restore_validation \
  --target-bucket restore-validation \
  --cleanup-after-validation \
  --evidence-dir /evidence
```

The two-file HTTP form above is development-only. For protected staging, both commands must use
the full Compose stack so the final overlay replaces `MC_HOST_source` with HTTPS and mounts only
the CA certificate into the operations container:

## Protected staging backup and restore

Set `APP_ENV=staging`. The deployment secret owner must provision every protected-stack MinIO
credential pair: `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`, `MINIO_ACCESS_KEY` /
`MINIO_SECRET_KEY`, and `DR_MINIO_ACCESS_KEY` / `DR_MINIO_SECRET_KEY`. Access-key and secret
values must come from the deployment secret owner; never accept the development defaults or copy
values from `.env.example`. The deployment configuration owner must also provide `MINIO_TLS_DIR`
and `PROMETHEUS_CONFIG_FILE`. `MINIO_ENDPOINT` and `MINIO_BUCKET` may retain their
deployment-approved values; the protected overlay fixes `MINIO_SECURE` to `true`,
`MINIO_CA_CERT_FILE`, and `SSL_CERT_FILE` to the mounted in-container CA path. Then run:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.ops.yml -f docker-compose.observability.protected.yml --profile ops run --rm backup-restore backup
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.ops.yml -f docker-compose.observability.protected.yml --profile ops run --rm backup-restore restore --backup-dir /backups/<backup-id> --target-environment staging --target-database restore_validation --target-bucket restore-validation --cleanup-after-validation --evidence-dir /evidence
```

With `APP_ENV=staging`, `prod`, or `production`, the tool rejects an HTTP MinIO alias before any
backup or restore command runs. Production backup uses the same protected stack; restore remains
forbidden whenever either `APP_ENV` or `--target-environment` is `prod`/`production`. Do not omit
the protected overlay, add `--insecure`, or print the rendered `MC_HOST_source` value. A passing
configuration/unit test is not a capability receipt: preserve the real drill's non-secret read,
write, delete, checksum, RPO/RTO, and denied-scope evidence before making a least-privilege claim.

Restore is refused when either `APP_ENV` or `--target-environment` is production, when the target
does not use the `restore_` / `restore-` namespace, when a target already exists, or when a target
equals the recorded source. The success metric is updated only after dump, schema, Alembic,
configuration metadata, object checksum, optional health, and optional cleanup validation pass.
Evidence is written to the separate `dr-evidence` volume; the immutable backup directory is never
modified during restore. It records measured backup age as `rpo_seconds`, elapsed restore time as
`rto_seconds`, table row counts, Alembic revision, and explicit missing/orphaned/mismatched object
lists. `main_chain_smoke=not_provided` remains a release blocker until the true infrastructure E2E
artifact is paired with the drill.

If validation fails, the isolated database or bucket may be retained for diagnosis. Inspect it,
then remove only the exact `restore_` database and `restore-` bucket named by the failed command.
Never point this tool at a production target.

### Protected release DR receipt

The isolated operator that performs the real protected-environment drill owns
`knowledge-uploader.dr-release-source.v1`; the collector must not synthesize it. Its exact receipt
keys are defined in [the protected release runbook](protected-release.md#外部源收据-v1严格契约).
`recovery_pair_id` is an opaque identifier for one paired drill. It binds the two independent,
one-way markers `postgres_restore_point_sha256` and `minio_restore_point_sha256`; the hashes are
not required to be equal because a PostgreSQL LSN/time point and a MinIO version/snapshot marker
are different identifiers. Operators must hash each native marker separately and must never put
the raw LSN, bucket marker, off-site URI, key identifier, or credential in release evidence.

The pair is accepted only together with the table digest, zero missing/orphan/mismatched objects,
measured RPO/RTO and the passed main-chain smoke. The version-controlled
`ops/policies/dr-release-policy.json` sets the current release maxima to 300 seconds RPO and
600 seconds RTO. The receipt must bind the exact policy bytes through `policy_sha256`; a drill may
declare stricter targets, but neither target nor actual measurement may exceed the policy. The
collector copies the exact policy into release evidence, and OCI provenance plus authorization bind
the same source bytes, so policy or evidence replacement fails closed. The local logical restore
command does not prove these external fields. No real protected DR source receipt is present in this
repository, so this gate remains **PENDING** until a new isolated drill supplies it.

## Backup staleness alert

`KnowledgeUploaderBackupMissing` fires when no validated timestamp exists.
`KnowledgeUploaderBackupStale` fires when the most recent validated backup is older than 24 hours.
`KnowledgeUploaderBackupLastAttemptFailed` 在一次任务开始后即进入失败候选状态，只有完整校验
成功才改为成功，因此进程崩溃也会在两分钟内报警。当前工具只写
`knowledge_uploader_logical_restore_validation_*`；它不会刷新季度 DR drill 指标。完整 DR drill
时间戳只能由外部门禁在隔离服务 ready、主链 smoke、配对恢复点、RPO/RTO 全部验证后写入。
从未完成真实演练或成功证据超过 90 天仍会触发 missing/stale 告警。
Treat either as a data-protection incident: inspect the one-shot container log, confirm PostgreSQL
and MinIO health and capacity, repair the cause, create a fresh validated backup, and perform an
isolated restore drill before closing the incident.

The textfile metrics live in the `backup-metrics` volume and are scraped through the internal
`backup-metrics` service. A failed backup or restore never refreshes the corresponding timestamp.

## Disaster-recovery release status

DR-001 and DR-002 stay `待执行` until a protected-environment gate proves all of the following:

- daily encrypted full backup plus continuous WAL/PITR (or equivalent managed capability);
- immutable off-site retention with an explicit expiry policy;
- MinIO versioning and replication, or a coordinated snapshot paired to the database restore point;
- encrypted configuration values plus a separately controlled key version and a no-plaintext
  decrypt verification;
- a quarterly isolated drill within the version-controlled RPO/RTO policy, with zero
  missing/orphaned objects and a passed upload → review → RAGFlow protocol-mock smoke.

Do not change either acceptance item to passed merely because the logical backup command succeeds.
