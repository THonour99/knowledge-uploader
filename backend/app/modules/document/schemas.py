from __future__ import annotations

from pydantic import BaseModel


class DocumentModuleStatus(BaseModel):
    name: str = "document"
