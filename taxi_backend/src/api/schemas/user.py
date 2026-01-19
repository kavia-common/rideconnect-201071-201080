from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserPublic(BaseModel):
    id: UUID = Field(..., description="User id")
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    role: str = Field(..., description="Role: rider or driver")
    created_at: datetime = Field(..., description="Account creation timestamp")
