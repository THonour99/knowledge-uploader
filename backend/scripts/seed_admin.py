from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import AsyncSessionFactory, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.modules.audit.models import AuditLog  # noqa: E402
from app.modules.user.models import User  # noqa: E402


@dataclass(frozen=True)
class SeedAdminArgs:
    email: str
    name: str
    department: str | None
    password: str


def parse_args() -> SeedAdminArgs:
    parser = argparse.ArgumentParser(description="Create or promote the first system_admin user.")
    parser.add_argument("--email", required=True, help="Admin email address.")
    parser.add_argument("--name", default="System Admin", help="Admin display name.")
    parser.add_argument("--department", default=None, help="Optional department.")
    parsed = parser.parse_args()

    email = parsed.email.strip().lower()
    password = os.getenv("SEED_ADMIN_PASSWORD", "")
    if "@" not in email:
        parser.error("--email must be a valid email address")
    settings = get_settings()
    email_domain = email.rsplit("@", 1)[1]
    if email_domain not in normalized_csv(settings.allowed_email_domains):
        parser.error("--email domain must be listed in ALLOWED_EMAIL_DOMAINS")
    if len(password) < settings.password_min_length:
        parser.error(
            "SEED_ADMIN_PASSWORD must be set and meet PASSWORD_MIN_LENGTH "
            f"({settings.password_min_length})"
        )
    return SeedAdminArgs(
        email=email,
        name=parsed.name.strip() or "System Admin",
        department=parsed.department.strip() if parsed.department else None,
        password=password,
    )


def normalized_csv(raw_value: str) -> set[str]:
    return {item.strip().lower() for item in raw_value.split(",") if item.strip()}


async def seed_admin(args: SeedAdminArgs) -> str:
    email_domain = args.email.rsplit("@", 1)[1]
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(User).where(User.email == args.email))
        user = result.scalar_one_or_none()
        created = user is None
        if user is None:
            user = User(
                name=args.name,
                email=args.email,
                email_domain=email_domain,
                password_hash=hash_password(args.password),
                department=args.department,
                role="system_admin",
                status="active",
                email_verified=True,
            )
            session.add(user)
            await session.flush()
        else:
            user.name = args.name
            user.email_domain = email_domain
            user.password_hash = hash_password(args.password)
            user.department = args.department
            user.role = "system_admin"
            user.status = "active"
            user.email_verified = True
            user.failed_login_count = 0
            user.locked_until = None
            user.session_version += 1

        session.add(
            AuditLog(
                actor_id=user.id,
                action="user.seed_system_admin",
                target_type="user",
                target_id=user.id,
                ip_address="bootstrap",
                user_agent="seed-admin-script",
                metadata_json={"email": user.email, "created": created},
                reason="bootstrap first system admin",
            )
        )
        await session.commit()
        return "created" if created else "updated"


async def main() -> int:
    args = parse_args()
    action = await seed_admin(args)
    await engine.dispose()
    sys.stdout.write(f"system_admin {action}: {args.email}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
