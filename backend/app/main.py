from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Knowledge Uploader", version="0.1.0")


@app.get("/api/system/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
