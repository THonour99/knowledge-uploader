from __future__ import annotations

from fastapi import FastAPI

from app.modules.auth.api import router as auth_router

app = FastAPI(title="Knowledge Uploader", version="0.1.0")

app.include_router(auth_router)


@app.get("/api/system/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
