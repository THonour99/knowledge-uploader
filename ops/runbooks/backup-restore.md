# Backup and restore runbook

This runbook covers validated PostgreSQL and MinIO backups. A backup is not reported as
successful until its database dump has been restored into an isolated verification database,
its object mirror has been hashed, and the manifest has passed the secret-leak guard.

## Create a validated backup

Start the normal PostgreSQL and MinIO services, then run the one-shot operations container:

```bash
docker compose up -d postgres minio
docker compose -f docker-compose.yml -f docker-compose.ops.yml --profile ops run --rm \
  backup-restore backup
```

The backup is written to the `backups` volume only after validation. The manifest records table
row counts and row digests, the Alembic revision, object key/size/ETag/SHA-256 metadata, and only
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

Restore is refused when either `APP_ENV` or `--target-environment` is production, when the target
does not use the `restore_` / `restore-` namespace, when a target already exists, or when a target
equals the recorded source. The success metric is updated only after dump, schema, Alembic,
configuration metadata, object checksum, optional health, and optional cleanup validation pass.
Evidence is written to the separate `dr-evidence` volume; the immutable backup directory is never
modified during restore. It records measured backup age as `rpo_seconds`, elapsed restore time as
`rto_seconds`, table digests, Alembic revision, and explicit missing/orphaned/mismatched object
lists. `main_chain_smoke=not_provided` remains a release blocker until the true infrastructure E2E
artifact is paired with the drill.

If validation fails, the isolated database or bucket may be retained for diagnosis. Inspect it,
then remove only the exact `restore_` database and `restore-` bucket named by the failed command.
Never point this tool at a production target.

## Backup staleness alert

`KnowledgeUploaderBackupMissing` fires when no validated timestamp exists.
`KnowledgeUploaderBackupStale` fires when the most recent validated backup is older than 24 hours.
`KnowledgeUploaderBackupLastAttemptFailed` 在一次任务开始后即进入失败候选状态，只有完整校验
成功才改为成功，因此进程崩溃也会在两分钟内报警。恢复演练同样记录 last-attempt；从未演练
或成功证据超过 90 天会触发 missing/stale 告警。
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
- a quarterly isolated drill within agreed RPO/RTO, with zero missing/orphaned objects and a passed
  upload → review → RAGFlow protocol-mock smoke.

Do not change either acceptance item to passed merely because the logical backup command succeeds.
