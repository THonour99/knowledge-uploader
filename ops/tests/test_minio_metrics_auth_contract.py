from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
TOKEN_DIR = "/run/secrets/minio-metrics"
TOKEN_FILE = f"{TOKEN_DIR}/token"
TOKEN_VOLUME_RW = f"minio-metrics-auth:{TOKEN_DIR}"
TOKEN_VOLUME_RO = f"{TOKEN_VOLUME_RW}:ro"
MINIO_MC_IMAGE = (
    "minio/mc:RELEASE.2024-04-18T16-45-29Z"
    "@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8"
)
MINIO_SERVER_IMAGE = (
    "minio/minio:RELEASE.2024-04-18T19-09-19Z"
    "@sha256:036a068d7d6b69400da6bc07a480bee1e241ef3c341c41d988ed11f520f85124"
)


def _yaml(path: str) -> dict[str, object]:
    value = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _minio_job(path: str) -> dict[str, object]:
    config = _yaml(path)
    jobs = config["scrape_configs"]
    assert isinstance(jobs, list)
    job = next(item for item in jobs if isinstance(item, dict) and item.get("job_name") == "minio")
    return job


def test_base_compose_generates_a_non_leaking_minio_metrics_token_file() -> None:
    compose_source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "/app/scripts/minio_bootstrap.py" not in compose_source
    assert "/app/scripts/minio_metrics_token_init.py" not in compose_source
    compose = _yaml("docker-compose.yml")
    services = compose["services"]
    assert isinstance(services, dict)
    minio = services["minio"]
    bootstrap = services["minio-bootstrap"]
    initializer = services["minio-metrics-token-init"]
    operational = services["operational-metrics"]
    assert isinstance(minio, dict)
    assert isinstance(bootstrap, dict)
    assert isinstance(initializer, dict)
    assert isinstance(operational, dict)

    assert minio["environment"]["MINIO_PROMETHEUS_AUTH_TYPE"] == "jwt"
    assert minio["image"] == f"${{MINIO_SERVER_IMAGE:-{MINIO_SERVER_IMAGE}}}"
    backend_build = compose["x-backend-build"]
    assert isinstance(backend_build, dict)
    backend_build_args = backend_build["args"]
    assert isinstance(backend_build_args, dict)
    assert backend_build_args["MINIO_MC_IMAGE"] == (f"${{MINIO_MC_IMAGE:-{MINIO_MC_IMAGE}}}")
    assert (
        minio["environment"]["MINIO_ROOT_USER"]
        != (compose["x-app-environment"]["MINIO_ACCESS_KEY"])
    )
    assert (
        minio["environment"]["MINIO_ROOT_PASSWORD"]
        != (compose["x-app-environment"]["MINIO_SECRET_KEY"])
    )
    assert bootstrap["image"] == "${BACKEND_IMAGE:-knowledge-uploader-backend:dev}"
    assert bootstrap["entrypoint"] == ["python", "-m", "scripts.minio_bootstrap"]
    assert "command" not in bootstrap
    assert bootstrap["restart"] == "no"
    assert bootstrap["depends_on"]["minio"]["condition"] == "service_healthy"
    bootstrap_source = (ROOT / "backend/scripts/minio_bootstrap.py").read_text(encoding="utf-8")
    for required in (
        "strict_minio_base_url",
        "strict_json_object",
        'allowed_hosts={"minio"}',
        "allowed_ports={9000}",
        "ACCESS_KEY_PATTERN.fullmatch(root_user)",
        "ACCESS_KEY_PATTERN.fullmatch(access_key)",
        "SECRET_KEY_PATTERN.fullmatch(root_password)",
        "SECRET_KEY_PATTERN.fullmatch(secret_key)",
        "root_user in DEFAULT_ROOT_USERS",
        "root_password in DEFAULT_ROOT_PASSWORDS",
        "access_key in DEFAULT_DATA_USERS",
        "secret_key in DEFAULT_DATA_SECRETS",
        "_verify_exact_bucket_policy(",
        'set(policy) != {"Statement", "Version"}',
        'set(statement) != {"Action", "Effect", "Resource"}',
        'statement.get("Effect") != "Allow"',
        "len(statements) != 2",
        "len(value) != len(set(value))",
        "actual != expected",
        "allow_empty: bool = False",
        "if allow_empty:",
        "allow_empty=True",
        '["admin", "group", "list", "bootstrap"]',
        '["admin", "group", "remove", "bootstrap", group, access_key]',
        '["admin", "user", "remove", "bootstrap", access_key]',
        '"entities", "bootstrap", "--policy", policy',
        "if groups or users - {access_key}:",
        '["admin", "policy", "remove", "bootstrap", POLICY_NAME]',
        '"create", "bootstrap", POLICY_NAME, str(policy_path)',
        '"attach", "bootstrap", POLICY_NAME, "--user", access_key',
        "policies != {POLICY_NAME}",
        "users != {access_key} or groups",
        "tempfile.mkdtemp(",
        "tempfile.mkstemp(",
        "os.fchmod(descriptor, 0o600)",
        "os.fsync(stream.fileno())",
        "policy_path.unlink(missing_ok=True)",
        "shutil.rmtree(working_directory",
        "subprocess.Popen(",
        "stdin=subprocess.DEVNULL",
        "tempfile.TemporaryFile()",
        "stdout=stdout_stream",
        "stderr=stderr_stream",
        "_bounded_output_sizes(",
        'start_new_session=os.name == "posix"',
        "_cleanup_communicate(",
        "except BootstrapInterrupted:",
        "raise BootstrapInterrupted from cleanup_error",
        "except BaseException as error:",
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
    ):
        assert required in bootstrap_source
    for forbidden in ("print(", "logger.", "logging.", "--insecure"):
        assert forbidden not in bootstrap_source
    assert bootstrap["environment"]["MINIO_ROOT_USER"] == (minio["environment"]["MINIO_ROOT_USER"])
    assert (
        bootstrap["environment"]["MINIO_ACCESS_KEY"]
        == (compose["x-app-environment"]["MINIO_ACCESS_KEY"])
    )
    assert services["backend-api"]["depends_on"]["minio-bootstrap"] == {
        "condition": "service_completed_successfully"
    }
    assert initializer["image"] == "${BACKEND_IMAGE:-knowledge-uploader-backend:dev}"
    assert initializer["entrypoint"] == [
        "python",
        "-m",
        "scripts.minio_metrics_token_init",
    ]
    assert "command" not in initializer
    assert initializer["restart"] == "no"
    assert initializer["depends_on"]["minio"]["condition"] == "service_healthy"
    assert initializer["volumes"] == [TOKEN_VOLUME_RW]
    assert "MINIO_METRICS_TOKEN_ROTATE" not in initializer["environment"]
    assert "MINIO_ACCESS_KEY" not in initializer["environment"]
    assert "MINIO_SECRET_KEY" not in initializer["environment"]
    assert initializer["depends_on"]["minio-bootstrap"] == {
        "condition": "service_completed_successfully"
    }

    initializer_source = (ROOT / "backend/scripts/minio_metrics_token_init.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "is_semantic_time_bound_jwt",
        "strict_minio_base_url",
        'allowed_hosts={"minio"}',
        "allowed_ports={9000}",
        "class _NoRedirect(HTTPRedirectHandler)",
        "ProxyHandler({})",
        "response.status != 200",
        "response.geturl() != metrics_url",
        "ssl.create_default_context(cafile=str(_validated_ca_file()))",
        "MC_CONFIG_DIR",
        "COMMAND_TIMEOUT_SECONDS = 30.0",
        "subprocess.Popen(",
        "stdin=subprocess.DEVNULL",
        "tempfile.TemporaryFile()",
        "stdout=stdout_stream",
        "stderr=stderr_stream",
        "_bounded_output_sizes(",
        'start_new_session=os.name == "posix"',
        "_cleanup_communicate(",
        "except TokenInitializationInterrupted:",
        "raise TokenInitializationInterrupted from cleanup_error",
        "except BaseException as error:",
        "tempfile.mkdtemp(",
        "shutil.rmtree(working_directory)",
        "tempfile.mkstemp(",
        'prefix=".token.tmp."',
        "os.fsync(stream.fileno())",
        "_open_token_directory()",
        "os.fchmod(descriptor, 0o755)",
        "os.fchown(stream.fileno(), 65534, 65534)",
        "os.fchmod(stream.fileno(), 0o440)",
        "temporary_path.replace(TOKEN_PATH)",
        "temporary_path.unlink(missing_ok=True)",
        'for name in ("SIGHUP", "SIGINT", "SIGTERM")',
        "except BaseException:",
        "sys.exit(1)",
    ):
        assert required in initializer_source
    assert initializer_source.count("os.fsync(stream.fileno())") == 2
    for forbidden in (
        "MINIO_METRICS_TOKEN_ROTATE",
        ".rotation.lock",
        "print(",
        "logger.",
        "logging.",
    ):
        assert forbidden not in initializer_source

    assert operational["depends_on"]["minio-metrics-token-init"] == {
        "condition": "service_completed_successfully"
    }
    assert operational["volumes"] == [TOKEN_VOLUME_RO]
    assert operational["environment"]["MINIO_ACCESS_KEY"] == ("metrics-bearer-only-no-data-plane")
    assert operational["environment"]["MINIO_SECRET_KEY"] == ("metrics-bearer-only-no-data-plane")
    app_environment = compose["x-app-environment"]
    assert "MINIO_METRICS_BEARER_TOKEN_FILE" not in app_environment
    assert operational["environment"]["MINIO_METRICS_BEARER_TOKEN_FILE"] == TOKEN_FILE
    for service_name, service in services.items():
        if service_name == "operational-metrics" or not isinstance(service, dict):
            continue
        environment = service.get("environment")
        if isinstance(environment, dict):
            assert "MINIO_METRICS_BEARER_TOKEN_FILE" not in environment

    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MINIO_PROMETHEUS_AUTH_TYPE: public" not in source
    assert "MINIO_METRICS_BEARER_TOKEN:" not in source
    assert "Bearer eyJ" not in source


def test_observability_and_e2e_consumers_mount_only_the_token_file_volume() -> None:
    observability = _yaml("docker-compose.observability.yml")
    services = observability["services"]
    assert isinstance(services, dict)
    assert "minio" not in services
    prometheus = services["prometheus"]
    assert isinstance(prometheus, dict)
    assert TOKEN_VOLUME_RO in prometheus["volumes"]
    assert prometheus["depends_on"]["minio-metrics-token-init"] == {
        "condition": "service_completed_successfully"
    }

    e2e = (ROOT / "docker-compose.e2e.yml").read_text(encoding="utf-8")
    assert "MINIO_PROMETHEUS_AUTH_TYPE: jwt" in e2e
    assert "MINIO_PROMETHEUS_AUTH_TYPE: public" not in e2e
    assert f"- {TOKEN_VOLUME_RW}" in e2e
    assert f"- {TOKEN_VOLUME_RO}" in e2e
    assert "/e2e-certs/ca.crt:ro" in e2e
    assert "minio-metrics-token-init:" in e2e
    assert "condition: service_completed_successfully" in e2e
    assert "MINIO_METRICS_BEARER_TOKEN:" not in e2e


def test_every_minio_prometheus_job_uses_the_same_bearer_file() -> None:
    expected = {
        "type": "Bearer",
        "credentials_file": TOKEN_FILE,
    }
    for path in (
        "ops/observability/prometheus.yml",
        "ops/observability/prometheus.protected.yml",
    ):
        assert _minio_job(path)["authorization"] == expected


def test_protected_initializer_trusts_the_private_minio_ca_without_disabling_tls() -> None:
    protected = _yaml("docker-compose.observability.protected.yml")
    services = protected["services"]
    assert isinstance(services, dict)
    initializer = services["minio-metrics-token-init"]
    bootstrap = services["minio-bootstrap"]
    minio = services["minio"]
    assert isinstance(initializer, dict)
    assert isinstance(bootstrap, dict)
    assert isinstance(minio, dict)
    required_root = {
        "MINIO_ROOT_USER": "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}",
        "MINIO_ROOT_PASSWORD": ("${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"),
    }
    assert minio["environment"] == required_root
    assert bootstrap["environment"] == {
        **required_root,
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_SECURE": "true",
        "MINIO_CA_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
        "SSL_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
    }
    assert initializer["environment"] == {
        **required_root,
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_SECURE": "true",
        "MINIO_CA_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
        "SSL_CERT_FILE": "/run/secrets/minio-ca/ca.crt",
    }

    volumes = initializer["volumes"]
    assert isinstance(volumes, list)
    assert volumes == [
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}" "/ca.crt:/run/secrets/minio-ca/ca.crt:ro"
    ]
    assert bootstrap["volumes"] == volumes

    source = (ROOT / "docker-compose.observability.protected.yml").read_text(encoding="utf-8")
    assert "insecure" not in source.lower()


def test_protected_minio_tls_covers_server_health_and_every_backend_client() -> None:
    protected = _yaml("docker-compose.observability.protected.yml")
    services = protected["services"]
    assert isinstance(services, dict)

    minio = services["minio"]
    assert isinstance(minio, dict)
    assert set(minio["volumes"]) == {
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/public.crt:"
        "/root/.minio/certs/public.crt:ro",
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/private.key:"
        "/root/.minio/certs/private.key:ro",
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt:"
        "/root/.minio/certs/CAs/protected-ca.crt:ro",
    }
    healthcheck = " ".join(minio["healthcheck"]["test"])
    assert "--cacert /root/.minio/certs/CAs/protected-ca.crt" in healthcheck
    assert "https://minio:9000/minio/health/cluster" in healthcheck
    assert "--insecure" not in healthcheck and " -k " not in healthcheck

    expected_ca = (
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt:" "/run/secrets/minio-ca/ca.crt:ro"
    )
    for service_name in (
        "rabbitmq-topology",
        "backend-api",
        "outbox-dispatcher",
        "operational-metrics",
        "worker-document",
        "worker-ai",
        "worker-ragflow",
        "worker-notification",
        "scheduler",
    ):
        service = services[service_name]
        assert service["environment"]["MINIO_SECURE"] == "true"
        assert service["environment"]["MINIO_CA_CERT_FILE"] == ("/run/secrets/minio-ca/ca.crt")
        assert service["volumes"] == [expected_ca]


def test_arm64_overlay_covers_the_initializer_and_both_consumers() -> None:
    compose = _yaml("docker-compose.arm64.yml")
    services = compose["services"]
    assert isinstance(services, dict)
    for service in (
        "operational-metrics",
        "minio",
        "minio-bootstrap",
        "minio-metrics-token-init",
    ):
        assert services[service]["platform"] == "linux/arm64"


def test_example_environment_contains_only_a_token_file_reference() -> None:
    source = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"MINIO_METRICS_BEARER_TOKEN_FILE={TOKEN_FILE}" in source
    assert "MINIO_METRICS_TOKEN_ROTATE" not in source
    assert "MINIO_METRICS_BEARER_TOKEN=" not in source


def test_root_credentials_and_writable_token_volume_are_least_privilege() -> None:
    base = _yaml("docker-compose.yml")
    services = base["services"]
    assert isinstance(services, dict)
    privileged = {"minio", "minio-bootstrap", "minio-metrics-token-init"}

    for service_name, raw_service in services.items():
        if not isinstance(raw_service, dict):
            continue
        environment = raw_service.get("environment", {})
        assert isinstance(environment, dict)
        if service_name not in privileged:
            assert "MINIO_ROOT_USER" not in environment
            assert "MINIO_ROOT_PASSWORD" not in environment
        volumes = raw_service.get("volumes", [])
        assert isinstance(volumes, list)
        if service_name == "minio-metrics-token-init":
            assert TOKEN_VOLUME_RW in volumes
        else:
            assert TOKEN_VOLUME_RW not in volumes

    protected = _yaml("docker-compose.observability.protected.yml")
    protected_services = protected["services"]
    assert isinstance(protected_services, dict)
    services_with_root = {
        name
        for name, raw_service in protected_services.items()
        if isinstance(raw_service, dict)
        and isinstance(raw_service.get("environment"), dict)
        and (
            "MINIO_ROOT_USER" in raw_service["environment"]
            or "MINIO_ROOT_PASSWORD" in raw_service["environment"]
        )
    }
    assert services_with_root == privileged


def test_protected_minio_runbook_uses_complete_compose_stack_and_exact_semantics() -> None:
    source = (ROOT / "ops/runbooks/observability.md").read_text(encoding="utf-8")
    protected_section = source.split(
        "\u6240\u6709 protected \u547d\u4ee4\u90fd\u5fc5\u987b\u53e0\u52a0\u5b8c\u6574\u4e09\u4e2a "
        "Compose \u6587\u4ef6\uff1a",
        1,
    )[1]
    protected_section = protected_section.split("\u6307\u6807\u6807\u7b7e\u53ea\u5141\u8bb8", 1)[0]
    commands = [
        line.strip()
        for line in protected_section.splitlines()
        if line.strip().startswith("docker compose ")
    ]
    assert commands
    required_files = (
        "-f docker-compose.yml",
        "-f docker-compose.observability.yml",
        "-f docker-compose.observability.protected.yml",
    )
    for command in commands:
        assert all(fragment in command for fragment in required_files)

    for forbidden in (
        "MINIO_METRICS_TOKEN_ROTATE",
        ".rotation.lock",
        "PROMETHEUS_TLS_DIR",
        "--force-recreate operational-metrics prometheus",
    ):
        assert forbidden not in source
    for required in (
        "\u65e7 JWT \u5728\u5176 `exp` \u524d\u4ecd\u53ef\u8fd4\u56de 200",
        "\u65e7 JWT \u4e0e\u65b0 JWT \u5747\u4e3a 200",
        "\u8f6e\u6362\u524d token \u4e3a 403",
        "\u65b0 token \u4e3a 200",
        "\u6d88\u8d39\u8005\u5bb9\u5668 ID \u5237\u65b0\u524d\u540e\u4e0d\u53d8",
        "\u539f\u5730\u81ea\u52a8\u6062\u590d",
        "\u7ef4\u62a4\u7a97",
        "\u56de\u6eda",
    ):
        assert required in source
