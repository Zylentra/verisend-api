import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from sqlmodel import select
from verisend.models.db_models import OrgMembership, Organization, User
from verisend.models.responses import MeResponse, UserOrgResponse
from verisend.utils.auth import Authenticated
from verisend.utils.blob_storage import BlobStorageContainer
from verisend.utils.clerk import ClerkDep
from verisend.utils.db import AsyncSession
from verisend.models.roles import Role

logger = logging.getLogger(__name__)

TAGS = [
    {
        "name": "Users",
        "description": "User profile and vault endpoints",
    },
]

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=MeResponse)
async def get_me(
    auth: Authenticated,
    session: AsyncSession,
):
    """Get the authenticated user's profile including org memberships.
    Creates the local user record on first sign-in (Clerk manages the actual account).
    """
    user = await session.get(User, auth.user_id)
    if not user:
        user = User(id=auth.user_id, email=auth.email or "")
        session.add(user)
        await session.commit()
        await session.refresh(user)

    result = await session.exec(
        select(OrgMembership, Organization)
        .join(Organization)
        .where(OrgMembership.user_id == user.id)
    )

    orgs = [
        UserOrgResponse(
            org_id=org.id,
            name=org.name,
            is_owner=org.owner_id == user.id,
        )
        for membership, org in result.all()
    ]

    return MeResponse(
        id=str(user.id),
        email=user.email,
        orgs=orgs,
    )


@router.post("/me/downgrade", status_code=status.HTTP_204_NO_CONTENT)
async def downgrade_to_user(
    auth: Authenticated,
    clerk: ClerkDep,
):
    """Clear the org_user role from Clerk metadata, reverting to standard user."""
    clerk.set_user_role(auth.user_id, Role.USER)


# ---- Vault ----


def _vault_path(user_id: str) -> str:
    return f"vaults/{user_id}/vault.json"


@router.put("/me/vault", status_code=status.HTTP_204_NO_CONTENT)
async def save_vault(
    request: Request,
    auth: Authenticated,
    container: BlobStorageContainer,
):
    """Save the user's vault data."""
    body = await request.body()
    blob_client = container.get_blob_client(_vault_path(auth.user_id))
    blob_client.upload_blob(body, overwrite=True)


@router.get("/me/vault")
async def get_vault(
    auth: Authenticated,
    container: BlobStorageContainer,
):
    """Get the user's vault data. Returns empty object for new users."""
    blob_client = container.get_blob_client(_vault_path(auth.user_id))

    if not blob_client.exists():
        return Response(content=b"{}", media_type="application/json")

    data = blob_client.download_blob().readall()
    return Response(content=data, media_type="application/json")
