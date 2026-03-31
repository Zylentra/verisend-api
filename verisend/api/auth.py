import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from verisend.settings import settings
from verisend.utils.db import AsyncSession
from verisend.utils.keycloak_admin import KeycloakAdminDep
from verisend.utils.email import send_magic_link_email
from verisend.models.db_models import LoginToken, User
from uuid import UUID
from verisend.models.requests import SendMagicLinkRequest
from verisend.models.responses import (
    SendMagicLinkResponse,
    VerifyTokenResponse,
    UserInfo,
)

logger = logging.getLogger(__name__)

TAGS = [
    {
        "name": "Authentication",
        "description": "Magic link authentication endpoints",
    },
]

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/send-magic-link", response_model=SendMagicLinkResponse)
async def send_magic_link(
    request: SendMagicLinkRequest,
    session: AsyncSession,
    keycloak: KeycloakAdminDep,
):
    """
    Send magic link to user's email.
    Creates Keycloak account if user doesn't exist.
    """
    email = request.email.lower()

    # Find or create user in Keycloak (sync calls → offload to thread)
    kc_user = await asyncio.to_thread(keycloak.find_user_by_email, email)
    if not kc_user:
        kc_user = await asyncio.to_thread(keycloak.create_user, email)

    kc_user_id = UUID(kc_user["id"])

    # Ensure local user record exists
    db_user = await session.get(User, kc_user_id)
    if not db_user:
        db_user = User(id=kc_user_id, email=email)
        session.add(db_user)

    # Generate secure random token
    token = secrets.token_urlsafe(32)

    # Store token in database
    login_token = LoginToken(
        token=token,
        user_id=str(kc_user_id),
        email=email,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        used=False,
    )
    session.add(login_token)
    await session.commit()

    # Build magic link and send email
    magic_link = f"{settings.app_url}/auth/verify?token={token}"
    await send_magic_link_email(email, magic_link)

    return SendMagicLinkResponse(
        message="Magic link sent! Check your email.",
        email=email,
    )


@router.get("/verify", response_model=VerifyTokenResponse)
async def verify_magic_link(
    token: str,
    session: AsyncSession,
    keycloak: KeycloakAdminDep,
):
    """
    Verify magic link token and authenticate user.
    Returns Keycloak access token for frontend.
    """
    statement = select(LoginToken).where(LoginToken.token == token)
    result = await session.exec(statement)
    login_token = result.first()

    if not login_token:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    if login_token.used:
        raise HTTPException(
            status_code=400,
            detail="This link has already been used. Please request a new one.",
        )

    if login_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="This link has expired. Please request a new one.",
        )

    # Capture values before commit (avoids lazy-load after session closes)
    user_id = login_token.user_id

    # Mark token as used
    login_token.used = True
    session.add(login_token)
    await session.commit()

    # Generate Keycloak access token for user (sync calls → offload to thread)
    try:
        keycloak_token = await asyncio.to_thread(keycloak.get_user_tokens, user_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Authentication failed: {e}",
        )

    # Get user details
    user = await asyncio.to_thread(keycloak.get_user_by_id, user_id)

    return VerifyTokenResponse(
        access_token=keycloak_token["access_token"],
        refresh_token=keycloak_token["refresh_token"],
        expires_in=keycloak_token["expires_in"],
        user=UserInfo(
            id=user["id"],
            email=user["email"],
            first_name=user.get("firstName", ""),
            last_name=user.get("lastName", ""),
        ),
    )
