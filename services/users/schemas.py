from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    gender: str = Field(default="不透露", max_length=40)
    age_at_registration: int | None = Field(default=None, ge=0, le=120, strict=True)
    profile_note: str = Field(default="", max_length=1000)


class ProfilePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    gender: str | None = Field(default=None, max_length=40)
    age_at_registration: int | None = Field(default=None, ge=0, le=120, strict=True)
    profile_note: str | None = Field(default=None, max_length=1000)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class DeleteConfirmation(BaseModel):
    confirm_user_id: str = Field(min_length=1, max_length=128)
