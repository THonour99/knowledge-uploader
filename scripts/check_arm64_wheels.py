"""Offline ARM64 dependency guard for locked Python requirements files."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BANNED_PATTERNS: dict[str, str] = {
    "psycopg2": "Use psycopg[binary] v3 instead of psycopg2 packages.",
    "python-magic": "Use filetype instead of python-magic packages.",
    "mysqlclient": "Project uses PostgreSQL; mysqlclient is not allowed.",
    "pycrypto": "Use cryptography instead of pycrypto.",
    "m2crypto": "Use cryptography instead of m2crypto.",
}

ARM64_ALLOWLIST: dict[str, str] = {
    "aiosmtplib": "2.0.0",
    "alembic": "1.13.0",
    "anyio": "4.0.0",
    "argon2-cffi": "23.1.0",
    "asyncpg": "0.29.0",
    "bcrypt": "4.1.0",
    "celery": "5.3.0",
    "cryptography": "42.0.0",
    "email-validator": "2.1.0",
    "factory-boy": "3.3.0",
    "faker": "25.0.0",
    "fastapi": "0.110.0",
    "filetype": "1.2.0",
    "freezegun": "1.4.0",
    "httpx": "0.27.0",
    "invoke": "2.2.0",
    "kombu": "5.3.0",
    "minio": "7.2.0",
    "mypy": "1.10.0",
    "passlib": "1.7.4",
    "pika": "1.3.0",
    "psycopg": "3.1.0",
    "pydantic": "2.6.0",
    "pydantic-settings": "2.2.0",
    "pyjwt": "2.8.0",
    "pytest": "8.0.0",
    "pytest-asyncio": "0.23.0",
    "pytest-cov": "5.0.0",
    "pytest-mock": "3.12.0",
    "python-dateutil": "2.9.0",
    "python-multipart": "0.0.9",
    "redis": "5.0.0",
    "ruff": "0.4.0",
    "slowapi": "0.1.9",
    "sqlalchemy": "2.0.0",
    "structlog": "24.1.0",
    "tenacity": "8.2.0",
    "tiktoken": "0.6.0",
    "types-passlib": "1.7.0",
    "types-python-dateutil": "2.9.0",
    "types-redis": "4.6.0",
    "uvicorn": "0.27.0",
}

SKIPPED_PREFIXES = ("-", "git+", "http://", "https://")
REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.-]+)"
    r"(?:\[[A-Za-z0-9_,.\-\s]+\])?"
    r"\s*(?P<operator>==|~=|>=|<=|!=|>|<)?\s*"
    r"(?P<version>[A-Za-z0-9_.!*+\-]+)?"
)


@dataclass(frozen=True)
class RequirementEntry:
    path: Path
    line_number: int
    raw: str
    name: str
    operator: str | None
    version: str | None


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def strip_inline_comment(line: str) -> str:
    if " #" not in line:
        return line.strip()
    return line.split(" #", 1)[0].strip()


def parse_version(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", version.split("!", 1)[-1])
    return tuple(int(part) for part in parts)


def version_at_least(version: str, minimum: str) -> bool:
    current = parse_version(version)
    required = parse_version(minimum)
    width = max(len(current), len(required))
    return current + (0,) * (width - len(current)) >= required + (0,) * (width - len(required))


def parse_requirement_line(path: Path, line_number: int, line: str) -> RequirementEntry | None:
    cleaned = strip_inline_comment(line)
    if not cleaned or cleaned.startswith(SKIPPED_PREFIXES):
        return None
    cleaned = cleaned.split(";", 1)[0].strip()
    match = REQUIREMENT_RE.match(cleaned)
    if match is None:
        return None
    name = normalize_name(match.group("name"))
    return RequirementEntry(
        path=path,
        line_number=line_number,
        raw=line.strip(),
        name=name,
        operator=match.group("operator"),
        version=match.group("version"),
    )


def parse_requirements(path: Path) -> list[RequirementEntry]:
    entries: list[RequirementEntry] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        entry = parse_requirement_line(path, line_number, line)
        if entry is not None:
            entries.append(entry)
    return entries


def is_banned(name: str) -> str | None:
    for pattern, reason in BANNED_PATTERNS.items():
        if name == pattern or name.startswith(f"{pattern}-"):
            return reason
    return None


def check_dependency(entry: RequirementEntry) -> CheckResult:
    banned_reason = is_banned(entry.name)
    if banned_reason is not None:
        return CheckResult(False, banned_reason)
    if entry.operator != "==" or entry.version is None:
        return CheckResult(False, "Dependency must be pinned with == for offline ARM64 checks.")
    minimum = ARM64_ALLOWLIST.get(entry.name)
    if minimum is None:
        return CheckResult(
            False,
            "Dependency is not in the offline ARM64 allowlist; review before adding it.",
        )
    if not version_at_least(entry.version, minimum):
        return CheckResult(False, f"Version is below ARM64 allowlist minimum {minimum}.")
    return CheckResult(True, f"allowlisted, minimum {minimum}")


def check_requirement_file(path: Path) -> tuple[list[str], list[str]]:
    if not path.exists():
        return [], [f"{path}: file does not exist"]
    entries = parse_requirements(path)
    print(f"\n=== {path} ({len(entries)} packages) ===")
    failures: list[str] = []
    for entry in entries:
        result = check_dependency(entry)
        marker = "OK" if result.ok else "FAIL"
        version = entry.version if entry.version is not None else "unlocked"
        print(f"  [{marker}] {entry.name}=={version}: {result.message}")
        if not result.ok:
            failures.append(f"{entry.path}:{entry.line_number} {entry.raw} - {result.message}")
    return [entry.name for entry in entries], failures


def check_package(name: str, version: str) -> int:
    entry = RequirementEntry(
        path=Path("<package>"),
        line_number=1,
        raw=f"{name}=={version}",
        name=normalize_name(name),
        operator="==",
        version=version,
    )
    result = check_dependency(entry)
    marker = "OK" if result.ok else "FAIL"
    print(f"[{marker}] {entry.name}=={version}: {result.message}")
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline ARM64 dependency checker.")
    parser.add_argument("requirements", nargs="*", help="requirements files to check")
    parser.add_argument("--package", help="Single package name to check")
    parser.add_argument("--version", help="Pinned package version for --package")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.package or args.version:
        if not args.package or not args.version:
            parser.error("--package and --version must be provided together")
        return check_package(args.package, args.version)
    if not args.requirements:
        parser.error("Provide at least one requirements file or --package with --version")

    failures: list[str] = []
    total_packages = 0
    for raw_path in args.requirements:
        entries, path_failures = check_requirement_file(Path(raw_path))
        total_packages += len(entries)
        failures.extend(path_failures)

    if failures:
        print(f"\nFailures ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"\nAll {total_packages} checked dependencies are ARM64 allowlisted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
