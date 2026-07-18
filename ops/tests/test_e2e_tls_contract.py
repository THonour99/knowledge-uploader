from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import yaml
from cryptography import x509


def _load_certificate_generator() -> ModuleType:
    path = Path(__file__).parents[2] / "backend" / "scripts" / "generate_e2e_certificates.py"
    spec = importlib.util.spec_from_file_location("generate_e2e_certificates_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load E2E certificate generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ephemeral_ca_issues_every_tls_service_certificate(tmp_path: Path) -> None:
    generator = _load_certificate_generator()
    output = tmp_path / "certificates"

    metadata = generator.generate_certificates(output)

    assert metadata["status"] == "generated"
    assert set(metadata["certificates"]) == {"gateway", "minio", "ragflow", "smtp"}
    assert len(str(metadata["ca_sha256"])) == 64
    assert len(str(metadata["certificate_bundle_sha256"])) == 64
    ca = x509.load_pem_x509_certificate((output / "ca.crt").read_bytes())
    ca_subject_key = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    ca_authority_key = ca.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
    assert ca_authority_key.key_identifier == ca_subject_key.digest
    expected_dns_names = {
        "gateway": "nginx",
        "minio": "minio",
        "ragflow": "mock-ragflow",
        "smtp": "mock-smtp",
    }
    for service, dns_name in expected_dns_names.items():
        certificate = x509.load_pem_x509_certificate((output / f"{service}.crt").read_bytes())
        alternatives = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        assert certificate.issuer == ca.subject
        subject_key = certificate.extensions.get_extension_for_class(
            x509.SubjectKeyIdentifier
        ).value
        authority_key = certificate.extensions.get_extension_for_class(
            x509.AuthorityKeyIdentifier
        ).value
        assert subject_key.digest
        assert authority_key.key_identifier == ca_subject_key.digest
        assert dns_name in alternatives.get_values_for_type(x509.DNSName)
        assert "localhost" in alternatives.get_values_for_type(x509.DNSName)
        assert (output / f"{service}.key").is_file()

    assert ".key" not in repr(metadata)
    assert "BEGIN CERTIFICATE" not in repr(metadata)


def test_e2e_tls_surfaces_contain_no_verification_bypass() -> None:
    root = Path(__file__).parents[2]
    paths = (
        root / "docker-compose.e2e.yml",
        root / "scripts" / "infrastructure_e2e_probe.py",
        root / "ops" / "e2e" / "mock_ragflow.py",
        root / "ops" / "e2e" / "mock_smtp.py",
    )
    source = chr(10).join(path.read_text(encoding="utf-8").lower() for path in paths)

    for forbidden in (
        "--insecure",
        "--no-check-certificate",
        "cert_none",
        "verify=false",
        "verify = false",
    ):
        assert forbidden not in source
    assert "https://mock-ragflow:9380" in source
    assert "smtp_tls" in source
    assert "minio_ca_cert_file" in source


def test_protected_prometheus_scrapes_minio_with_mounted_ca_and_hostname() -> None:
    root = Path(__file__).parents[2]
    development = yaml.safe_load(
        (root / "ops/observability/prometheus.yml").read_text(encoding="utf-8")
    )
    protected = yaml.safe_load(
        (root / "ops/observability/prometheus.protected.yml").read_text(encoding="utf-8")
    )

    development_minio = next(
        item for item in development["scrape_configs"] if item["job_name"] == "minio"
    )
    protected_minio = next(
        item for item in protected["scrape_configs"] if item["job_name"] == "minio"
    )

    assert development_minio.get("scheme", "http") == "http"
    assert "tls_config" not in development_minio
    assert protected_minio["scheme"] == "https"
    expected_authorization = {
        "type": "Bearer",
        "credentials_file": "/run/secrets/minio-metrics/token",
    }
    assert development_minio["authorization"] == expected_authorization
    assert protected_minio["authorization"] == expected_authorization
    assert protected_minio["tls_config"] == {
        "ca_file": "/etc/prometheus/tls/ca.crt",
        "server_name": "minio",
        "insecure_skip_verify": False,
    }
    assert protected_minio["static_configs"] == [{"targets": ["minio:9000"]}]


def test_protected_prometheus_compose_requires_read_only_config_and_tls_directory() -> None:
    root = Path(__file__).parents[2]
    compose = yaml.safe_load(
        (root / "docker-compose.observability.protected.yml").read_text(encoding="utf-8")
    )
    volumes = compose["services"]["prometheus"]["volumes"]

    assert (
        "${PROMETHEUS_CONFIG_FILE:?PROMETHEUS_CONFIG_FILE is required}"
        ":/etc/prometheus/prometheus.yml:ro"
    ) in volumes
    assert (
        "${MINIO_TLS_DIR:?MINIO_TLS_DIR is required}/ca.crt" ":/etc/prometheus/tls/ca.crt:ro"
    ) in volumes
    assert "PROMETHEUS_TLS_DIR" not in str(volumes)


def test_e2e_prometheus_runs_the_protected_config_with_ca_only_mount() -> None:
    compose = (Path(__file__).parents[2] / "docker-compose.e2e.yml").read_text(encoding="utf-8")

    assert (
        "./ops/observability/prometheus.protected.yml" ":/etc/prometheus/prometheus.yml:ro"
    ) in compose
    assert (
        "${E2E_CERT_DIR:?E2E_CERT_DIR is required}/ca.crt" ":/etc/prometheus/tls/ca.crt:ro"
    ) in compose
    assert "/etc/prometheus/tls/minio.key" not in compose
    assert "minio-metrics-auth:/run/secrets/minio-metrics:ro" in compose
    assert "MINIO_PROMETHEUS_AUTH_TYPE: jwt" in compose
    assert "MINIO_PROMETHEUS_AUTH_TYPE: public" not in compose
