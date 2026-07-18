from __future__ import annotations

import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/dgx-spark-device.yml"
PROTECTED_WORKFLOW = ROOT / ".github/workflows/protected-release.yml"
EXTERNAL_WORKFLOW = ROOT / ".github/workflows/protected-external-evidence.yml"
MAIN_WORKFLOW = ROOT / ".github/workflows/knowledge-uploader.yml"


def _workflow_job() -> dict[str, object]:
    payload = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("physical-arm64-validation")
    assert isinstance(job, dict)
    return job


def _protected_workflow_job() -> dict[str, object]:
    payload = yaml.load(PROTECTED_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("verify-protected-release")
    assert isinstance(job, dict)
    return job


def _main_workflow_job() -> dict[str, object]:
    payload = yaml.load(MAIN_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("lint-test-arm64")
    assert isinstance(job, dict)
    return job


def _main_release_workflow_job() -> dict[str, object]:
    payload = yaml.load(MAIN_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("build-release-oci")
    assert isinstance(job, dict)
    return job


def _main_local_workflow_job() -> dict[str, object]:
    payload = yaml.load(MAIN_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("local-act")
    assert isinstance(job, dict)
    return job


def _external_workflow_job() -> dict[str, object]:
    payload = yaml.load(EXTERNAL_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("collect-protected-evidence")
    assert isinstance(job, dict)
    return job


def _steps_by_name(job: dict[str, object]) -> tuple[list[str], dict[str, dict[str, object]]]:
    raw_steps = job.get("steps")
    assert isinstance(raw_steps, list)
    steps: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for raw_step in raw_steps:
        assert isinstance(raw_step, dict)
        name = raw_step.get("name")
        assert isinstance(name, str)
        order.append(name)
        steps[name] = raw_step
    return order, steps


def test_dgx_workflow_runs_real_e2e_before_device_verifier() -> None:
    job = _workflow_job()
    order, steps = _steps_by_name(job)
    e2e_name = "Run isolated full Compose and business E2E"
    verifier_name = "Verify physical host and release images"

    assert order.index(e2e_name) < order.index(verifier_name)
    e2e_run = steps[e2e_name].get("run")
    verifier_run = steps[verifier_name].get("run")
    assert isinstance(e2e_run, str)
    assert isinstance(verifier_run, str)
    assert "python scripts/run_infrastructure_e2e.py" in e2e_run
    assert "--allow-dirty-worktree" not in e2e_run
    assert "--evidence-dir artifacts" in e2e_run
    assert "python scripts/verify_dgx_spark.py" in verifier_run
    assert "--compose-e2e-evidence artifacts/infrastructure-e2e.json" in verifier_run


def test_dgx_workflow_loads_main_oci_and_binds_e2e_to_same_images_and_sha() -> None:
    job = _workflow_job()
    _order, steps = _steps_by_name(job)
    environment = job.get("env")
    assert isinstance(environment, dict)
    assert environment["BACKEND_E2E_IMAGE"] == (
        "knowledge-uploader-backend:dgx-${{ github.sha }}-${{ github.run_id }}"
    )
    assert environment["FRONTEND_E2E_IMAGE"] == (
        "knowledge-uploader-frontend:dgx-${{ github.sha }}-${{ github.run_id }}"
    )
    assert environment["MAIN_CI_RUN_ID"] == "${{ inputs.main_ci_run_id }}"
    assert environment["MAIN_CI_RUN_ATTEMPT"] == "${{ inputs.main_ci_run_attempt }}"
    trust = steps["Verify protected ref and successful main CI provenance"]
    assert trust.get("id") == "release-trust"
    trust_run = str(trust.get("run"))
    assert '--github-output "${GITHUB_OUTPUT}"' in trust_run
    download = steps["Download exact main CI OCI artifact"].get("with")
    assert isinstance(download, dict)
    assert download.get("artifact-ids") == (
        "${{ steps.release-trust.outputs.main_bundle_artifact_id }}"
    )
    assert "name" not in download

    load = str(steps["Verify and load the original ARM64 OCI manifests"].get("run"))
    e2e_run = str(steps["Run isolated full Compose and business E2E"].get("run"))
    verifier_run = str(steps["Verify physical host and release images"].get("run"))
    binding = str(steps["Bind physical runtime evidence to original OCI digests"].get("run"))
    assert "python scripts/release_oci.py load-arm64" in load
    assert '--workflow-run-id "${MAIN_CI_RUN_ID}"' in load
    assert '--backend-tag "${BACKEND_E2E_IMAGE}"' in load
    assert '--frontend-tag "${FRONTEND_E2E_IMAGE}"' in load
    all_runs = "\n".join(str(step.get("run", "")) for step in steps.values())
    assert "docker build" not in all_runs
    assert "${{ inputs.main_ci_run_id }}" not in all_runs
    assert "${{ inputs.main_ci_run_attempt }}" not in all_runs
    for command in (e2e_run, verifier_run):
        assert '--backend-image "${BACKEND_E2E_IMAGE}"' in command
        assert '--frontend-image "${FRONTEND_E2E_IMAGE}"' in command
        assert '--git-sha "${GITHUB_SHA}"' in command
    assert "python scripts/release_oci.py bind-dgx" in binding
    assert "artifacts/dgx-oci-consumption.json" in binding


def test_dgx_workflow_never_overwrites_shared_development_images() -> None:
    job = _workflow_job()
    _order, steps = _steps_by_name(job)
    environment = job.get("env")
    assert isinstance(environment, dict)
    for name in ("BACKEND_E2E_IMAGE", "FRONTEND_E2E_IMAGE"):
        image = environment[name]
        assert isinstance(image, str)
        assert "${{ github.sha }}" in image
        assert "${{ github.run_id }}" in image
        assert not image.endswith(":dev")
        assert not image.endswith(":latest")

    cleanup = steps["Cleanup isolated E2E image aliases"]
    assert cleanup.get("if") == "always()"
    cleanup_run = str(cleanup.get("run"))
    assert 'docker image rm --force "${BACKEND_E2E_IMAGE}" "${FRONTEND_E2E_IMAGE}"' in cleanup_run


def test_dgx_workflow_uploads_compose_rabbit_and_device_evidence() -> None:
    job = _workflow_job()
    _order, steps = _steps_by_name(job)
    upload = steps["Upload immutable device evidence"]
    assert upload.get("if") == "always()"
    options = upload.get("with")
    assert isinstance(options, dict)
    paths = str(options.get("path"))
    assert "artifacts/infrastructure-e2e.json" in paths
    assert "artifacts/rabbitmq-dlq-replay.json" in paths
    assert "artifacts/dgx-spark-evidence.json" in paths
    assert "artifacts/dgx-oci-consumption.json" in paths
    assert "artifacts/release-oci/release-oci-provenance.json" in paths
    assert "artifacts/trust/release-workflow-trust.json" in paths
    assert options.get("name") == (
        "dgx-spark-evidence-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert options.get("if-no-files-found") == "error"


def test_protected_workflow_downloads_dgx_and_independent_evidence() -> None:
    job = _protected_workflow_job()
    _order, steps = _steps_by_name(job)
    dgx = steps["Download DGX-bound infrastructure evidence"].get("with")
    external = steps["Download independent external evidence"].get("with")
    assert isinstance(dgx, dict)
    assert isinstance(external, dict)
    assert dgx.get("artifact-ids") == ("${{ steps.release-trust.outputs.dgx_artifact_id }}")
    assert "name" not in dgx
    assert dgx.get("run-id") == "${{ inputs.dgx_run_id }}"
    assert dgx.get("path") == "evidence/dgx"
    assert external.get("artifact-ids") == (
        "${{ steps.release-trust.outputs.external_artifact_id }}"
    )
    assert "name" not in external
    assert external.get("run-id") == "${{ inputs.external_evidence_run_id }}"
    assert external.get("path") == "evidence/external"


def test_protected_workflow_verifies_provenance_and_prevents_bundle_overwrite() -> None:
    job = _protected_workflow_job()
    assert job.get("environment") == {"name": "${{ inputs.environment }}"}
    environment = job.get("env")
    assert isinstance(environment, dict)
    assert environment["MAIN_CI_RUN_ID"] == "${{ inputs.main_ci_run_id }}"
    assert environment["DGX_RUN_ID"] == "${{ inputs.dgx_run_id }}"
    assert environment["DGX_RUN_ATTEMPT"] == "${{ inputs.dgx_run_attempt }}"
    assert environment["EXTERNAL_RUN_ID"] == "${{ inputs.external_evidence_run_id }}"
    assert environment["EXTERNAL_RUN_ATTEMPT"] == ("${{ inputs.external_evidence_run_attempt }}")
    order, steps = _steps_by_name(job)
    trust_step = steps["Verify protected ref and all workflow run provenance"]
    assert trust_step.get("id") == "release-trust"
    provenance = str(steps["Verify protected ref and all workflow run provenance"].get("run"))
    assert "scripts/release_workflow_trust.py fetch" in provenance
    assert '--ref-protected "${{ github.ref_protected }}"' in provenance
    assert ".github/workflows/knowledge-uploader.yml" not in provenance
    assert ".github/workflows/dgx-spark-device.yml" in provenance
    assert ".github/workflows/protected-external-evidence.yml" in provenance
    assert '--main-run-id "${MAIN_CI_RUN_ID}"' in provenance
    assert "dgx:${DGX_RUN_ID}:${DGX_RUN_ATTEMPT}:" in provenance
    assert "external:${EXTERNAL_RUN_ID}:${EXTERNAL_RUN_ATTEMPT}:" in provenance
    assert '--github-output "${GITHUB_OUTPUT}"' in provenance
    main_provenance = steps["Download main CI provenance summary"].get("with")
    assert isinstance(main_provenance, dict)
    assert main_provenance.get("artifact-ids") == (
        "${{ steps.release-trust.outputs.main_provenance_artifact_id }}"
    )
    assert "name" not in main_provenance
    assert "${{ inputs.main_ci_run_id }}" not in provenance
    assert "${{ inputs.external_evidence_run_id }}" not in provenance
    all_runs = "\n".join(str(step.get("run", "")) for step in steps.values())
    for input_name in (
        "inputs.main_ci_run_id",
        "inputs.main_ci_run_attempt",
        "inputs.dgx_run_id",
        "inputs.dgx_run_attempt",
        "inputs.external_evidence_run_id",
        "inputs.external_evidence_run_attempt",
    ):
        assert input_name not in all_runs

    verification_name = "Verify main OCI bundle and DGX provenance copies"
    verification = str(steps[verification_name].get("run"))
    assert order.index(verification_name) < order.index(
        "Assemble non-overlapping digest-bound evidence bundle"
    )
    assert verification.count("python scripts/release_oci.py verify") == 2
    assert "--bundle-dir evidence/main-bundle" in verification
    assert "--require-archives" in verification
    assert "--bundle-dir evidence/main-provenance" in verification
    assert "python scripts/release_workflow_trust.py verify" in verification
    assert "--current-role dgx" in verification
    assembly = str(steps["Assemble non-overlapping digest-bound evidence bundle"].get("run"))
    assert "evidence/dgx" in assembly
    assert "evidence/external" in assembly
    assert "evidence/main-provenance" in assembly
    assert "cross-bundle evidence collision" in assembly
    assert "shutil.copyfile" in assembly


def test_protected_workflow_runs_full_checker_with_complete_inventory() -> None:
    job = _protected_workflow_job()
    order, steps = _steps_by_name(job)
    inventory_name = "Assemble non-overlapping digest-bound evidence bundle"
    checker_name = "Run full protected release checker"
    external_readiness_name = "Enforce external service release readiness"
    authorization_name = "Issue short-lived digest-bound deployment authorization"
    assert order.index(inventory_name) < order.index(checker_name)
    assert order.index(checker_name) < order.index(external_readiness_name)
    assert order.index(external_readiness_name) < order.index(authorization_name)
    inventory = str(steps[inventory_name].get("run"))
    for filename in (
        "alertmanager-notification.json",
        "alertmanager.yml",
        "dr-release-policy.json",
        "dr-release.json",
        "rabbitmq-dlq-replay.json",
        "email-delivery.json",
        "promtool.json",
        "infrastructure-e2e.json",
        "dgx-spark-evidence.json",
        "dgx-oci-consumption.json",
        "release-oci-provenance.json",
        "release-workflow-trust.json",
    ):
        assert filename in inventory
    checker = str(steps[checker_name].get("run"))
    assert "python scripts/check_protected_release.py" in checker
    assert "--contract-only" not in checker
    assert 'gate_output = Path("evidence/protected-gate")' in inventory
    assert "gate_required = {" in inventory
    assert "--evidence-dir evidence/protected-gate" in checker
    assert "--alertmanager-config evidence/protected-gate/alertmanager.yml" in checker
    assert '--git-sha "${GITHUB_SHA}"' in checker
    assert '--environment "${RELEASE_ENVIRONMENT}"' in checker
    external_readiness = str(steps[external_readiness_name].get("run"))
    assert external_readiness == (
        "python scripts/check_external_release_readiness.py --require-ready"
    )
    authorization = str(steps[authorization_name].get("run"))
    assert "python scripts/release_oci.py authorize" in authorization
    assert "artifacts/release-authorization.json" in authorization


def test_protected_workflow_requires_real_mail_configuration() -> None:
    job = _protected_workflow_job()
    environment = job.get("env")
    assert isinstance(environment, dict)
    assert environment.get("ALLOW_EXTERNAL_LLM") == "false"
    assert environment.get("REQUIRE_EMAIL_VERIFICATION") == "true"
    assert environment.get("SMTP_HOST") == "${{ secrets.PROTECTED_SMTP_HOST }}"
    assert environment.get("SMTP_FROM") == "${{ secrets.PROTECTED_SMTP_FROM }}"
    assert environment.get("MINIO_ROOT_USER") == ("${{ secrets.PROTECTED_MINIO_ROOT_USER }}")
    assert environment.get("MINIO_ROOT_PASSWORD") == (
        "${{ secrets.PROTECTED_MINIO_ROOT_PASSWORD }}"
    )
    assert environment.get("MINIO_ACCESS_KEY") == ("${{ secrets.PROTECTED_MINIO_ACCESS_KEY }}")
    assert environment.get("MINIO_SECRET_KEY") == ("${{ secrets.PROTECTED_MINIO_SECRET_KEY }}")
    assert environment.get("MINIO_TLS_DIR") == "/run/secrets/minio-tls"
    assert (
        environment.get("PROMETHEUS_CONFIG_FILE") == "./ops/observability/prometheus.protected.yml"
    )
    _order, steps = _steps_by_name(job)
    dependency_install = str(steps["Install gate parser dependency"].get("run")).strip()
    assert dependency_install.startswith(
        "python -m pip install --require-hashes --only-binary=:all:"
    )
    for requirements_file in (
        "ops/requirements-protected-evidence.txt",
        "ops/requirements-protected-llm-evidence.txt",
    ):
        assert f"-r {requirements_file}" in dependency_install


def test_external_evidence_workflow_uses_protected_self_hosted_runner() -> None:
    job = _external_workflow_job()
    assert job.get("runs-on") == ["self-hosted", "Linux", "protected-evidence"]
    assert job.get("environment") == {"name": "${{ inputs.environment }}"}
    environment = job.get("env")
    assert isinstance(environment, dict)
    assert environment.get("PROMETHEUS_VALIDATOR_IMAGE") == (
        "prom/prometheus:v3.12.0"
        "@sha256:69f5241418838263316593f7274a304b095c40bcf22e57272865da91bd60a8ac"
    )
    assert environment.get("ALERTMANAGER_VALIDATOR_IMAGE") == (
        "prom/alertmanager:v0.28.1"
        "@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba"
    )
    _order, steps = _steps_by_name(job)
    dependency_install = str(steps["Install evidence parser dependency"].get("run")).strip()
    assert dependency_install == (
        "python -m pip install --require-hashes --only-binary=:all: "
        "-r ops/requirements-protected-evidence.txt"
    )
    dependency_lock = (ROOT / "ops/requirements-protected-evidence.txt").read_text(encoding="utf-8")
    assert "PyYAML==6.0.2" in dependency_lock
    assert dependency_lock.count("--hash=sha256:") == 2
    prepare = str(steps["Validate and assemble external evidence"].get("run"))
    assert "scripts/prepare_external_release_evidence.py" in prepare
    argv = shlex.split(prepare.replace("\\\n", " "), posix=True)
    assert argv == [
        "python",
        "scripts/prepare_external_release_evidence.py",
        "--source-dir",
        "$PROTECTED_EVIDENCE_SOURCE_DIR",
        "--output-dir",
        "artifacts/external",
        "--git-sha",
        "${{ github.sha }}",
        "--environment",
        "$RELEASE_ENVIRONMENT",
        "--collector-run-id",
        "${{ github.run_id }}",
        "--collector-run-attempt",
        "${{ github.run_attempt }}",
        "--prometheus-image",
        "$PROMETHEUS_VALIDATOR_IMAGE",
        "--alertmanager-image",
        "$ALERTMANAGER_VALIDATOR_IMAGE",
    ]
    upload = steps["Upload protected external evidence"].get("with")
    assert isinstance(upload, dict)
    assert upload.get("name") == (
        "protected-release-external-evidence-${{ github.sha }}-"
        "${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert upload.get("if-no-files-found") == "error"


def test_main_pr_ci_runs_static_release_contracts_without_claiming_physical_gate() -> None:
    job = _main_workflow_job()
    _order, steps = _steps_by_name(job)
    backend_checks = str(steps["Backend lint and tests"].get("run"))
    promtool = str(steps["Test Prometheus alert rules"].get("run"))
    compose = str(steps["Check Docker Compose syntax"].get("run"))
    assert "python -m pytest ops/tests" in backend_checks
    assert (
        "python -m ruff check backend/app backend/scripts scripts tasks.py ops/tests"
        in backend_checks
    )
    assert "/bin/promtool" in promtool
    assert "docker-compose.e2e.yml" in compose
    all_runs = "\n".join(str(step.get("run", "")) for step in steps.values())
    assert "run_infrastructure_e2e.py" not in all_runs
    assert "verify_dgx_spark.py" not in all_runs


def test_main_ci_runs_sha_scoped_protected_ui_acceptance() -> None:
    job = _main_workflow_job()
    order, steps = _steps_by_name(job)
    acceptance_name = "Run protected UI acceptance"
    upload_name = "Upload UI acceptance evidence"

    assert order.index("Frontend lint and tests") < order.index(acceptance_name)
    assert order.index(acceptance_name) < order.index(upload_name)

    acceptance = steps[acceptance_name]
    assert acceptance.get("if") == "${{ !env.ACT }}"
    environment = acceptance.get("env")
    assert isinstance(environment, dict)
    assert environment == {
        "E2E_ACCEPTANCE_MODE": "protected",
        "E2E_ARTIFACT_DIR": ("${{ runner.temp }}/knowledge-uploader-ui-${{ github.sha }}"),
        "E2E_BASE_URL": "http://127.0.0.1:4173",
        "E2E_GIT_SHA": "${{ github.sha }}",
    }
    command = acceptance.get("run")
    assert isinstance(command, str)
    for marker in (
        'test ! -e "${E2E_ARTIFACT_DIR}"',
        "npx --no-install playwright install --with-deps chromium",
        "npm run preview --prefix frontend -- --host 127.0.0.1 --port 4173",
        'curl --fail --silent --show-error "${E2E_BASE_URL}"',
        "npm run e2e:acceptance --prefix frontend",
        'test -f "${E2E_ARTIFACT_DIR}/evidence-manifest.json"',
    ):
        assert marker in command

    upload = steps[upload_name]
    assert upload.get("if") == "${{ success() && !env.ACT }}"
    assert upload.get("uses") == (
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    upload_inputs = upload.get("with")
    assert isinstance(upload_inputs, dict)
    assert upload_inputs["name"] == (
        "ui-acceptance-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert "${{ runner.temp }}/knowledge-uploader-ui-${{ github.sha }}" in str(
        upload_inputs["path"]
    )
    assert upload_inputs["if-no-files-found"] == "error"
    assert upload_inputs["retention-days"] == "14"


def test_every_main_ci_image_build_and_promtool_preflight_uses_pinned_digest() -> None:
    for job in (
        _main_workflow_job(),
        _main_release_workflow_job(),
        _main_local_workflow_job(),
    ):
        environment = job.get("env")
        assert isinstance(environment, dict)
        for name in ("PYTHON_RELEASE_IMAGE", "NODE_RELEASE_IMAGE", "NGINX_RELEASE_IMAGE"):
            assert "@sha256:" in str(environment[name])
        _order, steps = _steps_by_name(job)
        for step in steps.values():
            command = str(step.get("run", ""))
            if "docker buildx build" in command and "backend/Dockerfile" in command:
                assert '--build-arg PYTHON_IMAGE="${PYTHON_RELEASE_IMAGE}"' in command
            if "docker buildx build" in command and "frontend/Dockerfile" in command:
                assert '--build-arg NODE_IMAGE="${NODE_RELEASE_IMAGE}"' in command
                assert '--build-arg NGINX_IMAGE="${NGINX_RELEASE_IMAGE}"' in command
            if "/bin/promtool" in command:
                assert '"$PROMETHEUS_VALIDATOR_IMAGE"' in command
                assert "@sha256:" in str(environment["PROMETHEUS_VALIDATOR_IMAGE"])


def test_main_ci_builds_one_multiarch_oci_artifact_for_downstream_consumers() -> None:
    job = _main_release_workflow_job()
    assert job.get("needs") == "lint-test-arm64"
    assert job.get("if") == (
        "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}"
    )
    environment = job.get("env")
    assert isinstance(environment, dict)
    assert all(
        "@sha256:" in str(environment[name])
        for name in ("PYTHON_RELEASE_IMAGE", "NODE_RELEASE_IMAGE", "NGINX_RELEASE_IMAGE")
    )
    order, steps = _steps_by_name(job)
    backend = str(steps["Build backend OCI layout once with SBOM and provenance"].get("run"))
    frontend = str(steps["Build frontend OCI layout once with SBOM and provenance"].get("run"))
    seal = str(steps["Seal OCI indexes, platform manifests and source inputs"].get("run"))
    assert order.index("Build backend OCI layout once with SBOM and provenance") < order.index(
        "Seal OCI indexes, platform manifests and source inputs"
    )
    for command in (backend, frontend):
        assert command.count("docker buildx build") == 1
        assert "--platform linux/amd64,linux/arm64" in command
        assert "--provenance=mode=max" in command
        assert "--sbom=true" in command
        assert "--output type=oci" in command
        assert "--load" not in command
        assert "--push" not in command
    assert "python scripts/release_oci.py create" in seal
    assert "python scripts/release_oci.py verify" in seal
    assert "--require-archives" in seal
    for source_input in (
        "backend/Dockerfile",
        "backend/requirements.txt",
        "backend/app/core/jwt_validation.py",
        "backend/app/core/strict_json.py",
        "backend/app/core/minio_capacity_telemetry.py",
        "backend/app/core/minio_endpoint.py",
        "backend/scripts/minio_bootstrap.py",
        "backend/scripts/minio_metrics_token_init.py",
        "frontend/Dockerfile",
        "frontend/package-lock.json",
        "ops/policies/dr-release-policy.json",
    ):
        assert f"--source-input {source_input}" in seal
    assert '--build-arg PYTHON_IMAGE="${PYTHON_RELEASE_IMAGE}"' in backend
    assert '--build-arg NODE_IMAGE="${NODE_RELEASE_IMAGE}"' in frontend
    assert '--build-arg NGINX_IMAGE="${NGINX_RELEASE_IMAGE}"' in frontend
    bundle = steps["Upload immutable OCI bundle"].get("with")
    provenance = steps["Upload immutable provenance summary"].get("with")
    assert isinstance(bundle, dict)
    assert isinstance(provenance, dict)
    assert bundle.get("name") == (
        "release-oci-bundle-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert provenance.get("name") == (
        "release-oci-provenance-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}"
    )


def test_frontend_native_dependency_builder_uses_target_platform() -> None:
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    builder_lines = [
        line.strip() for line in dockerfile.splitlines() if line.strip().endswith(" AS builder")
    ]

    assert builder_lines == ["FROM --platform=$TARGETPLATFORM ${NODE_IMAGE} AS builder"]
    assert "npm ci" in dockerfile


def test_dgx_workflow_validates_protected_prometheus_mount_contract() -> None:
    workflow = (ROOT / ".github/workflows/dgx-spark-device.yml").read_text(encoding="utf-8")
    job = _workflow_job()
    environment = job.get("env")
    assert isinstance(environment, dict)

    assert (
        environment.get("PROMETHEUS_CONFIG_FILE") == "./ops/observability/prometheus.protected.yml"
    )
    assert environment.get("MINIO_TLS_DIR") == "/run/secrets/minio-tls"
    assert environment.get("MINIO_ROOT_USER") == ("${{ secrets.PROTECTED_MINIO_ROOT_USER }}")
    assert environment.get("MINIO_ROOT_PASSWORD") == (
        "${{ secrets.PROTECTED_MINIO_ROOT_PASSWORD }}"
    )
    assert environment.get("MINIO_ACCESS_KEY") == ("${{ secrets.PROTECTED_MINIO_ACCESS_KEY }}")
    assert environment.get("MINIO_SECRET_KEY") == ("${{ secrets.PROTECTED_MINIO_SECRET_KEY }}")
    assert "PROMETHEUS_TLS_DIR" not in workflow
    assert "-f docker-compose.observability.protected.yml" in workflow
