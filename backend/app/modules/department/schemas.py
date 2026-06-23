from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DepartmentStatus = Literal["active", "disabled"]


class DepartmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: DepartmentStatus | None = None


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    status: str
    created_at: datetime
    updated_at: datetime


class DepartmentListResponse(BaseModel):
    items: list[DepartmentResponse]
    total: int
    page: int
    page_size: int


class ReplaceManagedDepartmentsRequest(BaseModel):
    department_ids: list[uuid.UUID]


class ManagedDepartmentsResponse(BaseModel):
    user_id: uuid.UUID
    departments: list[DepartmentResponse]
