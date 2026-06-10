from __future__ import annotations

from app.core.permissions import AdminUserDep as AdminUserDep
from app.core.permissions import SystemAdminDep as SystemAdminDep

ADMIN_ROLES = frozenset({"knowledge_admin", "system_admin"})
SYSTEM_ADMIN_ROLE = "system_admin"
