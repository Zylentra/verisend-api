from fastapi import APIRouter
from . import (
    api,
    test,
    forms,
    orgs,
    standard_fields,
    users,
)


TAGS = [
    *api.TAGS,
    *test.TAGS,
    *forms.TAGS,
    *orgs.TAGS,
    *standard_fields.TAGS,
    *users.TAGS,
]

router = APIRouter()
router.include_router(api.router)
router.include_router(test.router)
router.include_router(forms.router)
router.include_router(orgs.router)
router.include_router(standard_fields.router)
router.include_router(users.router)