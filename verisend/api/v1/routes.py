from fastapi import APIRouter
from . import (
    test,
    forms,
    standard_fields,
    vault,
)


TAGS = [
    *test.TAGS,
    *forms.TAGS,
    *standard_fields.TAGS,
    *vault.TAGS,
]

router = APIRouter()
router.include_router(test.router)
router.include_router(forms.router)
router.include_router(standard_fields.router)
router.include_router(vault.router)