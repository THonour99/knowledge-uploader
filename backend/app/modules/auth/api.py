from __future__ import annotations

from fastapi import APIRouter

from .schemas import LoginRequest, LoginResponse, MockCurrentUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(payload: LoginRequest) -> LoginResponse:
    return LoginResponse(
        access_token=f"phase0-mock-token-for-{payload.email}",
        user=MockCurrentUser(email=payload.email),
    )


@router.post("/logout")
async def logout() -> dict[str, str]:
    return {"status": "ok"}
