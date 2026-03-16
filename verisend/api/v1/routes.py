from fastapi import APIRouter
from . import (
    test,
    forms,
    standard_fields,
)


TAGS = [
    *test.TAGS,
    *forms.TAGS,
    *standard_fields.TAGS,
]

router = APIRouter()
router.include_router(test.router)
router.include_router(forms.router)
router.include_router(standard_fields.router)