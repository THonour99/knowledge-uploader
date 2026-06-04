from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/admin/audit", tags=["audit"])
