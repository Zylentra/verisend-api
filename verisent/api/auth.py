"""
Auth endpoints — login flow is now handled entirely by Clerk.
This module is kept as a stub for route registration compatibility.
"""

from fastapi import APIRouter

TAGS = [
    {
        "name": "Authentication",
        "description": "Authentication is handled by Clerk (hosted service)",
    },
]

router = APIRouter(prefix="/auth", tags=["auth"])
