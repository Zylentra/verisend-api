"""
Clerk Backend API service for managing users and invitations.
Replaces the old Keycloak admin service.
"""

import logging
from typing import Annotated, Optional

from clerk_backend_api import Clerk
from fastapi import Depends

from verisend.settings import settings
from verisend.models.roles import Role

logger = logging.getLogger(__name__)


class ClerkService:
    """Service for managing users via Clerk Backend API"""

    def __init__(self):
        self.client = Clerk(bearer_auth=settings.clerk_secret_key.get_secret_value())

    def find_user_by_email(self, email: str) -> Optional[dict]:
        """Find a user by email address. Returns dict with id/email or None."""
        users = self.client.users.list(email_address=[email])
        if users and users.data:
            user = users.data[0]
            return {"id": user.id, "email": email}
        return None

    def create_user(self, email: str, role: Role | None = None) -> dict:
        """Create a new user with the given email. Optionally set a role."""
        kwargs = {
            "email_address": [email],
            "skip_password_requirement": True,
        }
        if role:
            kwargs["public_metadata"] = {"role": role.value}

        user = self.client.users.create(**kwargs)
        return {"id": user.id, "email": email}

    def set_user_role(self, user_id: str, role: Role) -> None:
        """Update a user's role in public_metadata."""
        self.client.users.update(
            user_id=user_id,
            public_metadata={"role": role.value},
        )
        logger.info(f"Set role '{role.value}' on user {user_id}")

    def get_user_by_id(self, user_id: str) -> dict:
        """Get user by Clerk user ID."""
        user = self.client.users.get(user_id=user_id)
        email = ""
        if user.email_addresses:
            email = user.email_addresses[0].email_address
        return {
            "id": user.id,
            "email": email,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
        }

    def create_invitation(self, email: str, redirect_url: str | None = None) -> dict:
        """Send an invitation email to a user via Clerk."""
        invitation = self.client.invitations.create(
            email_address=email,
            redirect_url=redirect_url or settings.app_url,
            ignore_existing=True,
        )
        return {"id": invitation.id, "email": email}


clerk_service = ClerkService()


def get_clerk() -> ClerkService:
    return clerk_service


ClerkDep = Annotated[ClerkService, Depends(get_clerk)]
