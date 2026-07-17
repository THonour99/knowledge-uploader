"""Generate an ephemeral CA and per-service TLS certificates for isolated E2E."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SERVER_IDENTITIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "minio": (("minio", "localhost"), ("127.0.0.1",)),
    "ragflow": (("mock-ragflow", "localhost"), ("127.0.0.1",)),
    "smtp": (("mock-smtp", "localhost"), ("127.0.0.1",)),
    "gateway": (("nginx", "localhost"), ("127.0.0.1",)),
}


def _server_certificate(
    *,
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    common_name: str,
    dns_names: tuple[str, ...],
    ip_addresses: tuple[str, ...],
    now: datetime,
) -> tuple[bytes, bytes, datetime]:
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    alternative_names: list[x509.GeneralName] = [
        *(x509.DNSName(name) for name in dns_names),
        *(x509.IPAddress(ipaddress.ip_address(address)) for address in ip_addresses),
    ]
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectAlternativeName(alternative_names), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    certificate_bytes = server_cert.public_bytes(serialization.Encoding.PEM)
    private_key_bytes = server_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return certificate_bytes, private_key_bytes, server_cert.not_valid_after_utc


def generate_certificates(output_dir: Path) -> dict[str, object]:
    output = output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Knowledge Uploader E2E Ephemeral CA")]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_bytes = ca_cert.public_bytes(serialization.Encoding.PEM)
    files: dict[str, bytes] = {"ca.crt": ca_bytes}
    certificate_digests: dict[str, str] = {}
    certificate_expirations: list[datetime] = []
    bundle_digest = hashlib.sha256()
    for identity, (dns_names, ip_addresses) in sorted(SERVER_IDENTITIES.items()):
        certificate_bytes, private_key_bytes, expires_at = _server_certificate(
            ca_cert=ca_cert,
            ca_key=ca_key,
            common_name=dns_names[0],
            dns_names=dns_names,
            ip_addresses=ip_addresses,
            now=now,
        )
        files[f"{identity}.crt"] = certificate_bytes
        files[f"{identity}.key"] = private_key_bytes
        certificate_digests[identity] = hashlib.sha256(certificate_bytes).hexdigest()
        certificate_expirations.append(expires_at)
        bundle_digest.update(identity.encode("ascii"))
        bundle_digest.update(b"\0")
        bundle_digest.update(certificate_bytes)
    for filename, content in files.items():
        path = output / filename
        path.write_bytes(content)
        path.chmod(0o600 if filename.endswith(".key") else 0o644)

    return {
        "status": "generated",
        "certificate_names": sorted(SERVER_IDENTITIES),
        "ca_sha256": hashlib.sha256(ca_bytes).hexdigest(),
        "certificate_sha256": certificate_digests["minio"],
        "certificate_bundle_sha256": bundle_digest.hexdigest(),
        "certificates": certificate_digests,
        "expires_at": min(certificate_expirations).isoformat(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = generate_certificates(args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
