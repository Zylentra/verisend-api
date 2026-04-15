from fastapi import APIRouter
from verisend.api import v1
from . import (
    admin,
    auth,
    utils,
)

TAGS = [
    *admin.TAGS,
    *auth.TAGS,
    *v1.TAGS,
    *utils.TAGS,
]

router = APIRouter()
router.include_router(admin.router)
router.include_router(auth.router)
router.include_router(v1.router, prefix="/v1")
router.include_router(utils.router)

__all__ = ["router", "TAGS"]