from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from verisend.models.db_models import StandardField
from verisend.models.requests import StandardFieldRequest, UpdateStandardFieldRequest
from verisend.models.responses import StandardFieldResponse
from verisend.utils.auth import RequireAdmin
from verisend.utils.db import AsyncSession


TAGS = [
    {
        "name": "Admin",
        "description": "Platform administration endpoints (admin only)",
    },
]

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/standard-fields", response_model=list[StandardFieldResponse])
async def list_standard_fields(
    auth: RequireAdmin,
    session: AsyncSession,
):
    """List all standard fields"""
    result = await session.exec(select(StandardField))
    return result.all()


@router.post(
    "/standard-fields",
    response_model=list[StandardFieldResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_standard_fields(
    body: list[StandardFieldRequest],
    auth: RequireAdmin,
    session: AsyncSession,
):
    """Create one or more standard fields"""
    fields = [StandardField(**item.model_dump()) for item in body]
    session.add_all(fields)
    await session.commit()
    for field in fields:
        await session.refresh(field)
    return fields


@router.patch(
    "/standard-fields/{field_id}",
    response_model=StandardFieldResponse,
)
async def update_standard_field(
    field_id: UUID,
    body: UpdateStandardFieldRequest,
    auth: RequireAdmin,
    session: AsyncSession,
):
    """Update a standard field (label, group, default_options, description)"""
    field = await session.get(StandardField, field_id)
    if not field:
        raise HTTPException(status_code=404, detail="Standard field not found")

    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(field, key, value)

    session.add(field)
    await session.commit()
    await session.refresh(field)
    return field
