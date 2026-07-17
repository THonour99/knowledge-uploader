from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

TEST_GIT_SHA = "a" * 40


def _load_preparer() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts/prepare_external_release_evidence.py"
    spec = importlib.util.spec_from_file_location("prepare_external_release_evidence", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load external evidence preparer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_sources(source: Path, *, generated_at: datetime | None = None) -> None:
    source.mkdir()
    timestamp = generated_at or datetime.now(UTC)
    alertmanager = (
        "route:\n  receiver: protected-webhook\nreceivers:\n"
        "  - name: protected-webhook\n    webhook_configs:\n"
        "      - url_file: /run/secrets/protected-webhook-url\n"
    )
    (source / "alertmanager.yml").write_text(alertmanager, encoding="utf-8")
    root = Path(__file__).parents[2]
    receipts: dict[str, dict[str, object]] = {
        "alertmanager-notification.json": {
            "alert_name": "KnowledgeUploaderProtectedReleaseProbe",
            "alert_fingerprint": "1" * 64,
            "receiver_name": "protected-webhook",
            "receiver_type": "webhook",
            "webhook_delivery_id_sha256": "2" * 64,
            "webhook_receipt_sha256": "3" * 64,
            "webhook_status_code": 202,
            "firing_at": timestamp.isoformat(),
            "delivered_at": timestamp.isoformat(),
            "resolved_at": timestamp.isoformat(),
        },
        "dr-release.json": {
            "backup_id": "20260716T000000Z-aabbccdd",
            "backup_manifest_sha256": "4" * 64,
            "restore_evidence_sha256": "5" * 64,
            "restore_started_at": timestamp.isoformat(),
            "restore_completed_at": timestamp.isoformat(),
            "rpo_seconds": 60,
            "rpo_target_seconds": 300,
            "rto_seconds": 120,
            "rto_target_seconds": 600,
            "policy_sha256": _sha256(root / "ops/policies/dr-release-policy.json"),
            "alembic_revision": "20260716o001",
            "database_tables_sha256": "6" * 64,
            "minio_missing_objects": 0,
            "minio_orphan_objects": 0,
            "minio_mismatched_objects": 0,
            "recovery_pair_id": "recovery-pair-001",
            "postgres_restore_point_sha256": "d" * 64,
            "minio_restore_point_sha256": "e" * 64,
            "postgres_pitr_enabled": True,
            "last_archived_at": timestamp.isoformat(),
            "full_backup_encrypted": True,
            "full_backup_immutable": True,
            "offsite_location_sha256": "7" * 64,
            "retention_until": (timestamp + timedelta(days=31)).isoformat(),
            "minio_versioning_enabled": True,
            "minio_replication_enabled": True,
            "coordinated_snapshot": False,
            "key_version_sha256": "8" * 64,
            "decrypt_validation": "passed",
            "plaintext_emitted": False,
            "main_chain_smoke": "passed",
            "cleanup_validation": "passed",
        },
        "email-delivery.json": {
            "registration_delivery": "passed",
            "password_reset_delivery": "passed",
            "registration_message_id_sha256": "9" * 64,
            "password_reset_message_id_sha256": "a" * 64,
            "registration_smtp_receipt_sha256": "b" * 64,
            "password_reset_smtp_receipt_sha256": "c" * 64,
            "registration_smtp_result": "accepted",
            "password_reset_smtp_result": "accepted",
            "registration_delivered_at": timestamp.isoformat(),
            "password_reset_delivered_at": timestamp.isoformat(),
            "persistent_message": True,
            "broker_expiry_at_or_before_token_expiry": True,
            "publisher_confirm": "passed",
            "encrypted_envelope_observed": True,
            "plaintext_token_observed": False,
            "dlq_plaintext_token_observed": False,
            "publish_failure_public_response_indistinguishable": True,
            "publish_failure_public_statuses": {
                "register": 201,
                "resend_verification": 200,
                "forgot_password": 200,
            },
            "publish_failure_metric_recorded": True,
            "retry_issued_fresh_token": True,
            "smtp_delivery_semantics": "at_most_once_attempt",
        },
        "validator-receipt.json": {
            "prometheus_config": "passed",
            "prometheus_rules": "passed",
            "alertmanager_config": "passed",
            "prometheus_config_sha256": _sha256(
                root / "ops/observability/prometheus.protected.yml"
            ),
            "prometheus_rules_sha256": _sha256(root / "ops/observability/alerts.yml"),
            "alertmanager_config_sha256": _sha256(source / "alertmanager.yml"),
            "prometheus_image": (
                "prom/prometheus:v3.12.0"
                "@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
            ),
            "prometheus_manifest_list_digest": (
                "sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
            ),
            "prometheus_image_id": "sha256:" + "b" * 64,
            "prometheus_image_os": "linux",
            "prometheus_image_architecture": "amd64",
            "prometheus_docker_architecture": "amd64",
            "alertmanager_image": (
                "prom/alertmanager:v0.28.1"
                "@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba"
            ),
            "alertmanager_manifest_list_digest": (
                "sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba"
            ),
            "alertmanager_image_id": "sha256:" + "c" * 64,
            "alertmanager_image_os": "linux",
            "alertmanager_image_architecture": "amd64",
            "alertmanager_docker_architecture": "amd64",
        },
    }
    contracts = {
        "alertmanager-notification.json": (
            "knowledge-uploader.alertmanager-webhook-source.v1",
            "alertmanager-webhook-receiver",
        ),
        "dr-release.json": (
            "knowledge-uploader.dr-release-source.v1",
            "backup-restore-drill",
        ),
        "email-delivery.json": (
            "knowledge-uploader.smtp-delivery-source.v1",
            "smtp-delivery-probe",
        ),
        "validator-receipt.json": (
            "knowledge-uploader.observability-validator-source.v1",
            "observability-validator",
        ),
    }
    for filename, receipt in receipts.items():
        schema, tool = contracts[filename]
        source_evidence = {
            "schema": schema,
            "generated_at": timestamp.isoformat(),
            "git_sha": TEST_GIT_SHA,
            "environment": "staging",
            "source_run_id": str(uuid.uuid4()),
            "source_run_attempt": 1,
            "source_tool": tool,
            "status": "passed",
            "receipt": receipt,
        }
        (source / filename).write_text(json.dumps(source_evidence), encoding="utf-8")


def _validator_evidence(
    reference: str,
    *,
    image_id_character: str,
    architecture: str = "amd64",
) -> dict[str, str]:
    return {
        "reference": reference,
        "manifest_list_digest": reference.rsplit("@", maxsplit=1)[1],
        "image_id": "sha256:" + image_id_character * 64,
        "operating_system": "linux",
        "architecture": architecture,
        "docker_architecture": architecture,
    }


def test_preparer_projects_safe_inputs_and_binds_validator_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_sources(source)
    original_dr = (source / "dr-release.json").read_bytes()

    def fake_checks(**kwargs: object) -> tuple[dict[str, str], dict[str, str]]:
        config = kwargs["alertmanager_config"]
        assert isinstance(config, Path)
        assert config.parent != output
        assert config.read_bytes() == (source / "alertmanager.yml").read_bytes()
        (source / "dr-release.json").write_text(
            "runner-secret-that-must-not-be-copied",
            encoding="utf-8",
        )
        return (
            _validator_evidence(
                preparer.PROMETHEUS_IMAGE,
                image_id_character="b",
            ),
            _validator_evidence(
                preparer.ALERTMANAGER_IMAGE,
                image_id_character="c",
            ),
        )

    monkeypatch.setattr(
        preparer,
        "_run_observability_checks",
        fake_checks,
    )

    files = preparer.prepare(
        source_dir=source,
        output_dir=output,
        git_sha=TEST_GIT_SHA,
        environment="staging",
        collector_run_id=9001,
        collector_run_attempt=1,
        prometheus_image=preparer.PROMETHEUS_IMAGE,
        alertmanager_image=preparer.ALERTMANAGER_IMAGE,
    )

    assert {path.name for path in files} == set(preparer.OUTPUT_FILES)
    promtool = json.loads((output / "promtool.json").read_text(encoding="utf-8"))
    validator_receipt = promtool["receipt"]
    assert promtool["schema"] == preparer.OUTPUT_SCHEMAS["validator-receipt.json"]
    assert promtool["collector_run_id"] == 9001
    assert promtool["collector_run_attempt"] == 1
    assert validator_receipt["prometheus_config"] == "passed"
    assert validator_receipt["prometheus_image_id"] == "sha256:" + "b" * 64
    assert validator_receipt["alertmanager_image_id"] == "sha256:" + "c" * 64
    assert validator_receipt["prometheus_image"] == preparer.PROMETHEUS_IMAGE
    assert (
        validator_receipt["prometheus_manifest_list_digest"]
        == (preparer.PROMETHEUS_IMAGE.rsplit("@", maxsplit=1)[1])
    )
    assert validator_receipt["prometheus_image_architecture"] == "amd64"
    assert validator_receipt["prometheus_docker_architecture"] == "amd64"
    dr_projection = json.loads((output / "dr-release.json").read_text(encoding="utf-8"))
    assert (output / "dr-release.json").read_bytes() != original_dr
    assert b"runner-secret" not in (output / "dr-release.json").read_bytes()
    assert dr_projection["schema"] == preparer.OUTPUT_SCHEMAS["dr-release.json"]
    assert dr_projection["source"]["file_sha256"] == hashlib.sha256(original_dr).hexdigest()
    assert dr_projection["receipt"]["backup_id"] == "20260716T000000Z-aabbccdd"
    policy_payload = (
        Path(__file__).parents[2] / "ops/policies/dr-release-policy.json"
    ).read_bytes()
    assert (output / "dr-release-policy.json").read_bytes() == policy_payload
    assert dr_projection["receipt"]["policy_sha256"] == hashlib.sha256(
        policy_payload
    ).hexdigest()
    assert validator_receipt["alertmanager_config_sha256"] == preparer._sha256(
        output / "alertmanager.yml"
    )
    assert set(promtool) == set(preparer.OUTPUT_COMMON_KEYS)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("policy_sha256", "f" * 64),
        ("rpo_target_seconds", 301),
        ("rto_target_seconds", 601),
        ("rpo_seconds", 301),
        ("rto_seconds", 601),
    ),
)
def test_dr_receipt_rejects_policy_mismatch_or_wider_limits(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    now = datetime.now(UTC)
    _write_sources(source, generated_at=now)
    evidence = json.loads((source / "dr-release.json").read_text(encoding="utf-8"))
    receipt = evidence["receipt"]
    receipt[field] = value
    policy_payload = (
        Path(__file__).parents[2] / "ops/policies/dr-release-policy.json"
    ).read_bytes()
    policy = preparer._load_dr_release_policy(policy_payload)

    with pytest.raises(preparer.EvidencePreparationError, match="receipt_dr-release"):
        preparer._validate_dr_receipt(
            receipt,
            now=now,
            policy=policy,
            policy_sha256=hashlib.sha256(policy_payload).hexdigest(),
        )


def test_dr_receipt_allows_stricter_self_reported_targets(tmp_path: Path) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    now = datetime.now(UTC)
    _write_sources(source, generated_at=now)
    evidence = json.loads((source / "dr-release.json").read_text(encoding="utf-8"))
    receipt = evidence["receipt"]
    receipt.update(
        {
            "rpo_seconds": 60,
            "rpo_target_seconds": 120,
            "rto_seconds": 120,
            "rto_target_seconds": 300,
        }
    )
    policy_payload = (
        Path(__file__).parents[2] / "ops/policies/dr-release-policy.json"
    ).read_bytes()

    preparer._validate_dr_receipt(
        receipt,
        now=now,
        policy=preparer._load_dr_release_policy(policy_payload),
        policy_sha256=hashlib.sha256(policy_payload).hexdigest(),
    )


def test_dr_policy_rejects_unsupported_schema() -> None:
    preparer = _load_preparer()
    payload = json.dumps(
        {
            "schema": "knowledge-uploader.dr-release-policy.v0",
            "max_rpo_seconds": 300,
            "max_rto_seconds": 600,
            "measurement": "restore drill",
            "owner": "platform-operations",
        }
    ).encode("utf-8")

    with pytest.raises(preparer.EvidencePreparationError, match="dr_release_policy"):
        preparer._load_dr_release_policy(payload)


def test_preparer_rejects_stale_or_prepopulated_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_sources(source, generated_at=datetime.now(UTC) - timedelta(hours=3))
    monkeypatch.setattr(
        preparer,
        "_run_observability_checks",
        lambda **_kwargs: (
            _validator_evidence(
                preparer.PROMETHEUS_IMAGE,
                image_id_character="b",
            ),
            _validator_evidence(
                preparer.ALERTMANAGER_IMAGE,
                image_id_character="c",
            ),
        ),
    )

    with pytest.raises(preparer.EvidencePreparationError, match="identity_"):
        preparer.prepare(
            source_dir=source,
            output_dir=output,
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=9001,
            collector_run_attempt=1,
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )

    _write_sources(tmp_path / "fresh")
    (output / "unexpected.txt").write_text("stale", encoding="utf-8")
    with pytest.raises(preparer.EvidencePreparationError, match="output_directory"):
        preparer.prepare(
            source_dir=tmp_path / "fresh",
            output_dir=output,
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=9001,
            collector_run_attempt=1,
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


def test_observability_checks_use_pinned_container_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    config = tmp_path / "alertmanager.yml"
    config.write_text("route: {}\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, step: str, timeout_seconds: float = 180.0) -> str:
        del step, timeout_seconds
        commands.append(command)
        if command[:3] == ["docker", "info", "--format"]:
            return "x86_64"
        if command[:4] == ["docker", "image", "inspect", "--format"]:
            character = "b" if "prometheus" in command[-1] else "c"
            return json.dumps(
                {
                    "Id": "sha256:" + character * 64,
                    "Os": "linux",
                    "Architecture": "amd64",
                }
            )
        return ""

    monkeypatch.setattr(preparer, "_run", fake_run)

    prometheus, alertmanager = preparer._run_observability_checks(
        alertmanager_config=config,
        prometheus_image=preparer.PROMETHEUS_IMAGE,
        alertmanager_image=preparer.ALERTMANAGER_IMAGE,
    )

    pulls = [command for command in commands if command[:2] == ["docker", "pull"]]
    runs = [command for command in commands if command[:3] == ["docker", "run", "--rm"]]
    assert len(pulls) == 2
    assert len(runs) == 3
    assert all("@sha256:" in command[-1] for command in pulls)
    assert any("/bin/promtool" in command for command in commands)
    assert any("/bin/amtool" in command for command in commands)
    assert prometheus["manifest_list_digest"].startswith("sha256:")
    assert alertmanager["manifest_list_digest"].startswith("sha256:")


@pytest.mark.parametrize(
    "prometheus_reference",
    (
        "prom/prometheus:v3.12.0",
        (
            "prom/prometheus:v3.12.0"
            "@sha256:dd4bced05dfaddf23a7ec50f87334993a4149f7fcfbf58456d1c8bafce91cd13"
        ),
    ),
)
def test_preparer_rejects_mutable_or_unapproved_single_platform_digest(
    tmp_path: Path,
    prometheus_reference: str,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    _write_sources(source)

    with pytest.raises(
        preparer.EvidencePreparationError,
        match="prometheus_image_reference",
    ):
        preparer.prepare(
            source_dir=source,
            output_dir=tmp_path / "output",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=9001,
            collector_run_attempt=1,
            prometheus_image=prometheus_reference,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


def test_observability_checks_reject_cache_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    config = tmp_path / "alertmanager.yml"
    config.write_text("route: {}\n", encoding="utf-8")
    monkeypatch.setattr(preparer, "_docker_architecture", lambda: "amd64")
    monkeypatch.setattr(
        preparer,
        "_pull_validator_image",
        lambda reference, **_kwargs: _validator_evidence(
            reference,
            image_id_character="b" if "prometheus" in reference else "c",
        ),
    )
    monkeypatch.setattr(preparer, "_run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        preparer,
        "_image_metadata",
        lambda reference, **_kwargs: _validator_evidence(
            reference,
            image_id_character="d" if "prometheus" in reference else "c",
        ),
    )

    with pytest.raises(
        preparer.EvidencePreparationError,
        match="prometheus_image_identity_changed",
    ):
        preparer._run_observability_checks(
            alertmanager_config=config,
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


def test_image_metadata_rejects_architecture_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    monkeypatch.setattr(
        preparer,
        "_run",
        lambda *_args, **_kwargs: json.dumps(
            {
                "Id": "sha256:" + "b" * 64,
                "Os": "linux",
                "Architecture": "arm64",
            }
        ),
    )

    with pytest.raises(preparer.EvidencePreparationError, match="prometheus_image"):
        preparer._image_metadata(
            preparer.PROMETHEUS_IMAGE,
            manifest_list_digest=preparer.PROMETHEUS_IMAGE.rsplit(
                "@",
                maxsplit=1,
            )[1],
            docker_architecture="amd64",
            step="prometheus_image",
        )


def test_preparer_rejects_inline_alertmanager_secret_before_copy(tmp_path: Path) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    _write_sources(source)
    (source / "alertmanager.yml").write_text(
        (
            "route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
            "    webhook_configs:\n      - url: https://hooks.example.test/private-token\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        preparer.EvidencePreparationError,
        match="alertmanager_inline_secret",
    ):
        preparer.prepare(
            source_dir=source,
            output_dir=tmp_path / "output",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=9001,
            collector_run_attempt=1,
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


@pytest.mark.parametrize(
    "header_block",
    (
        "Authorization:\n              values: [opaque-marker]",
        "Proxy-Authorization:\n              values: [opaque-marker]",
        "X-API-Key:\n              values: [opaque-marker]",
        "X-Auth-Token:\n              values: [opaque-marker]",
        "X-Correlation-ID:\n              secrets: [opaque-marker]",
    ),
)
def test_preparer_rejects_inline_http_header_secret(header_block: str) -> None:
    preparer = _load_preparer()
    config = (
        "route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
        "    webhook_configs:\n      - url_file: /run/secrets/webhook\n"
        "        http_config:\n          http_headers:\n"
        f"            {header_block}\n"
    ).encode()

    with pytest.raises(
        preparer.EvidencePreparationError,
        match="alertmanager_sensitive_http_header",
    ) as captured:
        preparer._reject_inline_alertmanager_secrets(config)

    assert "opaque-marker" not in str(captured.value)


def test_preparer_allows_http_header_secret_files() -> None:
    preparer = _load_preparer()
    config = (
        b"route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
        b"    webhook_configs:\n      - url_file: /run/secrets/webhook\n"
        b"        http_config:\n          http_headers:\n"
        b"            Authorization:\n"
        b"              files: [/run/secrets/authorization-header]\n"
    )

    preparer._reject_inline_alertmanager_secrets(config)


def test_preparer_rejects_sensitive_json_field_without_echoing_value(
    tmp_path: Path,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    _write_sources(source)
    evidence = json.loads((source / "dr-release.json").read_text(encoding="utf-8"))
    evidence["nested"] = {"api_key": "sk-must-never-appear"}
    (source / "dr-release.json").write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(
        preparer.EvidencePreparationError,
        match=r"sensitive_field_dr-release\.json",
    ) as captured:
        preparer.prepare(
            source_dir=source,
            output_dir=tmp_path / "output",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            collector_run_id=9001,
            collector_run_attempt=1,
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )

    assert "sk-must-never-appear" not in str(captured.value)


def _prepare_fixture(preparer: ModuleType, source: Path, output: Path) -> tuple[Path, ...]:
    return preparer.prepare(
        source_dir=source,
        output_dir=output,
        git_sha=TEST_GIT_SHA,
        environment="staging",
        collector_run_id=9001,
        collector_run_attempt=1,
        prometheus_image=preparer.PROMETHEUS_IMAGE,
        alertmanager_image=preparer.ALERTMANAGER_IMAGE,
    )


def test_preparer_rejects_unknown_and_duplicate_json_fields(tmp_path: Path) -> None:
    preparer = _load_preparer()
    unknown_source = tmp_path / "unknown"
    _write_sources(unknown_source)
    unknown = json.loads((unknown_source / "dr-release.json").read_text(encoding="utf-8"))
    unknown["unexpected"] = "not-allowed"
    (unknown_source / "dr-release.json").write_text(json.dumps(unknown), encoding="utf-8")

    with pytest.raises(preparer.EvidencePreparationError, match=r"schema_dr-release\.json"):
        _prepare_fixture(preparer, unknown_source, tmp_path / "unknown-output")

    duplicate_source = tmp_path / "duplicate"
    _write_sources(duplicate_source)
    path = duplicate_source / "dr-release.json"
    payload = path.read_text(encoding="utf-8")
    path.write_text(
        '{"schema":"knowledge-uploader.dr-release-source.v1",' + payload[1:],
        encoding="utf-8",
    )

    with pytest.raises(preparer.EvidencePreparationError, match=r"parse_dr-release\.json"):
        _prepare_fixture(preparer, duplicate_source, tmp_path / "duplicate-output")


def test_preparer_rejects_future_and_overlapping_source_runs(tmp_path: Path) -> None:
    preparer = _load_preparer()
    future_source = tmp_path / "future"
    _write_sources(future_source, generated_at=datetime.now(UTC) + timedelta(minutes=6))

    with pytest.raises(preparer.EvidencePreparationError, match="identity_"):
        _prepare_fixture(preparer, future_source, tmp_path / "future-output")

    overlap_source = tmp_path / "overlap"
    _write_sources(overlap_source)
    dr = json.loads((overlap_source / "dr-release.json").read_text(encoding="utf-8"))
    email_path = overlap_source / "email-delivery.json"
    email = json.loads(email_path.read_text(encoding="utf-8"))
    email["source_run_id"] = dr["source_run_id"]
    email_path.write_text(json.dumps(email), encoding="utf-8")

    with pytest.raises(preparer.EvidencePreparationError, match="source_run_overlap"):
        _prepare_fixture(preparer, overlap_source, tmp_path / "overlap-output")


def test_preparer_rejects_forged_validator_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    _write_sources(source)
    receipt_path = source / "validator-receipt.json"
    evidence = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence["receipt"]["prometheus_image_id"] = "sha256:" + "d" * 64
    receipt_path.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(
        preparer,
        "_run_observability_checks",
        lambda **_kwargs: (
            _validator_evidence(preparer.PROMETHEUS_IMAGE, image_id_character="b"),
            _validator_evidence(preparer.ALERTMANAGER_IMAGE, image_id_character="c"),
        ),
    )

    with pytest.raises(preparer.EvidencePreparationError, match="validator_receipt_mismatch"):
        _prepare_fixture(preparer, source, tmp_path / "output")


def test_preparer_rejects_pii_and_symlinked_sources(tmp_path: Path) -> None:
    preparer = _load_preparer()
    pii_source = tmp_path / "pii"
    _write_sources(pii_source)
    pii_path = pii_source / "dr-release.json"
    evidence = json.loads(pii_path.read_text(encoding="utf-8"))
    evidence["operator"] = "reviewer@example.test"
    pii_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(
        preparer.EvidencePreparationError,
        match=r"sensitive_value_dr-release\.json",
    ):
        _prepare_fixture(preparer, pii_source, tmp_path / "pii-output")

    symlink_source = tmp_path / "symlink"
    _write_sources(symlink_source)
    source_path = symlink_source / "dr-release.json"
    target_path = symlink_source / "dr-release.payload"
    source_path.replace(target_path)
    try:
        source_path.symlink_to(target_path.name)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")

    with pytest.raises(preparer.EvidencePreparationError, match=r"source_dr-release\.json"):
        _prepare_fixture(preparer, symlink_source, tmp_path / "symlink-output")
