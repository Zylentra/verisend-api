import hashlib
import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from verisend.models.db_models import Organization, OrgMembership, OrgApiKey, User
from verisend.settings import settings
from verisend.models.requests import (
    CreateOrgRequest,
    InviteMemberRequest,
    CreateOrgApiKeyRequest,
)
from verisend.models.responses import (
    OrgResponse,
    OrgMemberResponse,
    OrgApiKeyResponse,
    OrgApiKeyCreatedResponse,
)
from verisend.utils.auth import Authenticated, RequireOrgUser
from verisend.utils.db import AsyncSession
from verisend.utils.clerk import ClerkDep
from verisend.models.roles import Role


TAGS = [
    {
        "name": "Organizations",
        "description": "Organization management endpoints",
    },
]

router = APIRouter(prefix="/orgs", tags=["Organizations"])


async def _require_org_member(session: AsyncSession, org_id: UUID, user_id: str) -> OrgMembership:
    """Verify user is a member of the org, return membership."""
    result = await session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == user_id,
        )
    )
    membership = result.first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    return membership


async def _require_org_owner(session: AsyncSession, org: Organization, user_id: str) -> None:
    """Verify user is the owner of the org."""
    if org.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Only the organization owner can perform this action")


@router.post("", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: CreateOrgRequest,
    auth: Authenticated,
    session: AsyncSession,
    clerk: ClerkDep,
):
    """Create a new organization. The authenticated user becomes the owner."""
    user_id = auth.user_id

    # Promote user to org_user role in Clerk
    if auth.role != Role.ORG_USER and auth.role != Role.ADMIN:
        clerk.set_user_role(user_id, Role.ORG_USER)

    org = Organization(
        name=body.name,
        business_name=body.business_name,
        registration_number=body.registration_number,
        address=body.address,
        owner_id=user_id,
    )
    session.add(org)
    await session.flush()

    # Owner is also a member
    membership = OrgMembership(org_id=org.id, user_id=user_id)
    session.add(membership)

    await session.commit()
    await session.refresh(org)
    return org


@router.get("/{org_id}", response_model=OrgResponse)
async def get_org(
    org_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """Get organization details. Must be a member."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_member(session, org_id, auth.user_id)
    return org


@router.get("/{org_id}/members", response_model=list[OrgMemberResponse])
async def list_members(
    org_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """List org members."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_member(session, org_id, auth.user_id)

    result = await session.exec(
        select(OrgMembership, User).join(User).where(OrgMembership.org_id == org_id)
    )
    memberships = result.all()

    return [
        OrgMemberResponse(
            user_id=user.id,
            email=user.email,
            created_at=membership.created_at,
        )
        for membership, user in memberships
    ]


@router.post("/{org_id}/members", response_model=OrgMemberResponse, status_code=status.HTTP_201_CREATED)
async def invite_member(
    org_id: UUID,
    body: InviteMemberRequest,
    auth: RequireOrgUser,
    session: AsyncSession,
    clerk: ClerkDep,
):
    """Invite a user to the org by email. Owner only."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_owner(session, org, auth.user_id)

    email = body.email.lower()

    # Find or create user in Clerk with org_user role
    clerk_user = clerk.find_user_by_email(email)
    if not clerk_user:
        clerk_user = clerk.create_user(email, role=Role.ORG_USER)
    else:
        clerk.set_user_role(clerk_user["id"], Role.ORG_USER)

    clerk_user_id = clerk_user["id"]

    # Ensure local user record exists
    user = await session.get(User, clerk_user_id)
    if not user:
        result = await session.exec(select(User).where(User.email == email))
        user = result.first()
    if not user:
        user = User(id=clerk_user_id, email=email)
        session.add(user)

    # Check not already a member
    existing = await session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == clerk_user_id,
        )
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="User is already a member of this organization")

    # Create membership
    membership = OrgMembership(org_id=org_id, user_id=clerk_user_id)
    session.add(membership)

    await session.commit()
    await session.refresh(membership)

    # Send invitation email via Clerk
    clerk.create_invitation(email, redirect_url=settings.app_url)

    return OrgMemberResponse(
        user_id=clerk_user_id,
        email=email,
        created_at=membership.created_at,
    )


@router.post(
    "/{org_id}/api-keys",
    response_model=OrgApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    org_id: UUID,
    body: CreateOrgApiKeyRequest,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """Create an API key for the org. Owner only. Returns the raw key once."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_owner(session, org, auth.user_id)

    # Generate a random API key and store only the hash
    raw_key = secrets.token_urlsafe(48)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = OrgApiKey(
        org_id=org_id,
        name=body.name,
        key_hash=key_hash,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    return OrgApiKeyCreatedResponse(
        id=api_key.id,
        org_id=api_key.org_id,
        name=api_key.name,
        created_at=api_key.created_at,
        api_key=raw_key,
    )


@router.get("/{org_id}/api-keys", response_model=list[OrgApiKeyResponse])
async def list_api_keys(
    org_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """List API keys for the org. Owner only."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_owner(session, org, auth.user_id)

    result = await session.exec(select(OrgApiKey).where(OrgApiKey.org_id == org_id))
    return result.all()


@router.delete("/{org_id}/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    org_id: UUID,
    api_key_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """Delete an API key. Owner only."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_owner(session, org, auth.user_id)

    api_key = await session.get(OrgApiKey, api_key_id)
    if not api_key or api_key.org_id != org_id:
        raise HTTPException(status_code=404, detail="API key not found")

    await session.delete(api_key)
    await session.commit()
