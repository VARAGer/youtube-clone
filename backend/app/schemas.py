from typing import Literal
from pydantic import BaseModel, Field, field_validator
class Credentials(BaseModel):
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=8,max_length=128)
    @field_validator('email')
    @classmethod
    def email_valid(cls,v):
        v = v.strip().lower()
        if '@' not in v or '.' not in v.split('@')[-1] or any(c.isspace() for c in v):
            raise ValueError('Invalid email')
        return v
class Registration(Credentials):
    display_name: str = Field(min_length=1,max_length=80)
    @field_validator('display_name')
    @classmethod
    def name_valid(cls,v):
        if not v.strip():
            raise ValueError('Display name is required')
        return v.strip()
class VideoEdit(BaseModel):
    title: str | None = Field(default=None,min_length=1,max_length=200)
    description: str | None = Field(default=None,max_length=10000)
    visibility: Literal['public','private','unlisted'] | None = None
class CommentInput(BaseModel):
    body: str = Field(min_length=1,max_length=2000)
class EmailInput(BaseModel):
    email: str = Field(min_length=3,max_length=254)
class PlaylistInput(BaseModel):
    title: str = Field(min_length=1,max_length=150)
class ReportInput(BaseModel):
    reason: str = Field(min_length=3,max_length=1000)
class HideInput(BaseModel):
    hidden: bool
class RoleInput(BaseModel):
    role: Literal['user','moderator','admin']
