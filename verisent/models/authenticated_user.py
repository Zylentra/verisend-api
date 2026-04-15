from typing import Optional
from pydantic import BaseModel
from verisend.models.roles import Role

class AuthenticatedUser(BaseModel):
    """Model representing an authenticated user"""
    user_id: str
    auth_type: str
    authenticated: bool
    role: Role = Role.USER
    org_id: str | None = None  # set for org API key auth
    email: str | None = None
    name: str | None = None
    payload: Optional[dict] = None
    token: Optional[str] = None
    
    class Config:
        arbitrary_types_allowed = True