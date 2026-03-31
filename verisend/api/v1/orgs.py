import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from verisend.models.db_models import LoginToken, Organization, OrgMembership, OrgKeyGrant, OrgApiKey, User
from verisend.settings import settings
from verisend.utils.email import send_magic_link_email
from verisend.models.requests import (
    CreateOrgRequest,
    InviteMemberRequest,
    CreateKeyGrantRequest,
    CreateOrgApiKeyRequest,
)
from verisend.models.responses import (
    OrgResponse,
    OrgMemberResponse,
    KeyGrantResponse,
    OrgApiKeyResponse,
    OrgApiKeyCreatedResponse,
)
from verisend.utils.auth import Authenticated, RequireOrgUser
from verisend.utils.db import AsyncSession
from verisend.utils.keycloak_admin import KeycloakAdminDep
from verisend.models.roles import Role


TAGS = [
    {
        "name": "Organizations",
        "description": "Organization management endpoints",
    },
]

router = APIRouter(prefix="/orgs", tags=["Organizations"])


async def _require_org_member(session: AsyncSession, org_id: UUID, user_id: UUID) -> OrgMembership:
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


async def _require_org_owner(session: AsyncSession, org: Organization, user_id: UUID) -> None:
    """Verify user is the owner of the org."""
    if org.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Only the organization owner can perform this action")


@router.post("", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: CreateOrgRequest,
    auth: Authenticated,
    session: AsyncSession,
    keycloak: KeycloakAdminDep,
):
    """Create a new organization. The authenticated user becomes the owner."""
    user_id = UUID(auth.user_id)

    # Assign org_user role if the user doesn't already have it
    if auth.role != Role.ORG_USER:
        await asyncio.to_thread(keycloak.assign_role, auth.user_id, Role.ORG_USER)

    org = Organization(
        name=body.name,
        business_name=body.business_name,
        registration_number=body.registration_number,
        address=body.address,
        owner_id=user_id,
        public_key=body.public_key,
    )
    session.add(org)
    await session.flush()

    # Owner is also a member
    membership = OrgMembership(org_id=org.id, user_id=user_id)
    session.add(membership)

    # Owner's key grant
    key_grant = OrgKeyGrant(
        org_id=org.id,
        user_id=user_id,
        encrypted_org_private_key=body.encrypted_org_private_key,
    )
    session.add(key_grant)

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

    await _require_org_member(session, org_id, UUID(auth.user_id))
    return org


@router.get("/{org_id}/members", response_model=list[OrgMemberResponse])
async def list_members(
    org_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """List org members with their key grant status."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_member(session, org_id, UUID(auth.user_id))

    # Get all memberships with user data
    result = await session.exec(
        select(OrgMembership, User).join(User).where(OrgMembership.org_id == org_id)
    )
    memberships = result.all()

    # Get existing key grants for this org
    grant_result = await session.exec(
        select(OrgKeyGrant.user_id).where(OrgKeyGrant.org_id == org_id)
    )
    granted_user_ids = set(grant_result.all())

    return [
        OrgMemberResponse(
            user_id=user.id,
            email=user.email,
            has_public_key=user.public_key is not None,
            public_key=user.public_key,
            has_key_grant=user.id in granted_user_ids,
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
    keycloak: KeycloakAdminDep,
):
    """Invite a user to the org by email. Owner only."""
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _require_org_owner(session, org, UUID(auth.user_id))

    email = body.email.lower()

    # Find or create user in Keycloak
    kc_user = await asyncio.to_thread(keycloak.find_user_by_email, email)
    if not kc_user:
        kc_user = await asyncio.to_thread(keycloak.create_user, email)

    kc_user_id = UUID(kc_user["id"])

    # Ensure local user record exists (check by ID first, then by email)
    user = await session.get(User, kc_user_id)
    if not user:
        result = await session.exec(select(User).where(User.email == email))
        user = result.first()
    if not user:
        user = User(id=kc_user_id, email=email)
        session.add(user)

    # Check not already a member
    existing = await session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == kc_user_id,
        )
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="User is already a member of this organization")

    # Create membership
    membership = OrgMembership(org_id=org_id, user_id=kc_user_id)
    session.add(membership)

    # Capture user values before commit (avoids lazy-load after session expires attributes)
    user_public_key = user.public_key

    # Assign org_user role in Keycloak
    await asyncio.to_thread(keycloak.assign_role, str(kc_user_id), Role.ORG_USER)

    # Send magic link email to the invited user
    token = secrets.token_urlsafe(32)
    login_token = LoginToken(
        token=token,
        user_id=str(kc_user_id),
        email=email,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        used=False,
    )
    session.add(login_token)

    await session.commit()
    await session.refresh(membership)

    magic_link = f"{settings.app_url}/auth/verify?token={token}"
    await send_magic_link_email(email, magic_link)

    return OrgMemberResponse(
        user_id=kc_user_id,
        email=email,
        has_public_key=user_public_key is not None,
        public_key=user_public_key,
        has_key_grant=False,
        created_at=membership.created_at,
    )


@router.post(
    "/{org_id}/members/{user_id}/key-grant",
    response_model=KeyGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_key_grant(
    org_id: UUID,
    user_id: UUID,
    body: CreateKeyGrantRequest,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """
    Grant a member access to the org's encrypted private key.
    The caller must already have a key grant themselves.
    """
    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Verify caller has a key grant (meaning they have access to the org private key)
    caller_grant = await session.exec(
        select(OrgKeyGrant).where(
            OrgKeyGrant.org_id == org_id,
            OrgKeyGrant.user_id == UUID(auth.user_id),
        )
    )
    if not caller_grant.first():
        raise HTTPException(status_code=403, detail="You do not have key access to this organization")

    # Verify target user is a member
    await _require_org_member(session, org_id, user_id)

    # Verify target user has a public key
    target_user = await session.get(User, user_id)
    if not target_user or not target_user.public_key:
        raise HTTPException(status_code=400, detail="User has not set up their keypair yet")

    # Check no existing grant
    existing = await session.exec(
        select(OrgKeyGrant).where(
            OrgKeyGrant.org_id == org_id,
            OrgKeyGrant.user_id == user_id,
        )
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="User already has key access")

    key_grant = OrgKeyGrant(
        org_id=org_id,
        user_id=user_id,
        encrypted_org_private_key=body.encrypted_org_private_key,
    )
    session.add(key_grant)
    await session.commit()
    await session.refresh(key_grant)

    return KeyGrantResponse(
        org_id=key_grant.org_id,
        user_id=key_grant.user_id,
        created_at=key_grant.created_at,
    )


@router.get("/{org_id}/key-grant", response_model=CreateKeyGrantRequest)
async def get_my_key_grant(
    org_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """Get the authenticated user's key grant for this org."""
    result = await session.exec(
        select(OrgKeyGrant).where(
            OrgKeyGrant.org_id == org_id,
            OrgKeyGrant.user_id == UUID(auth.user_id),
        )
    )
    grant = result.first()
    if not grant:
        raise HTTPException(status_code=404, detail="No key grant found")

    return CreateKeyGrantRequest(encrypted_org_private_key=grant.encrypted_org_private_key)


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

    await _require_org_owner(session, org, UUID(auth.user_id))

    # Generate a random API key and store only the hash
    raw_key = secrets.token_urlsafe(48)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = OrgApiKey(
        org_id=org_id,
        name=body.name,
        key_hash=key_hash,
        public_key=body.public_key,
        encrypted_private_key=body.encrypted_private_key,
        encrypted_org_private_key=body.encrypted_org_private_key,
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

    await _require_org_owner(session, org, UUID(auth.user_id))

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

    await _require_org_owner(session, org, UUID(auth.user_id))

    api_key = await session.get(OrgApiKey, api_key_id)
    if not api_key or api_key.org_id != org_id:
        raise HTTPException(status_code=404, detail="API key not found")

    await session.delete(api_key)
    await session.commit()
