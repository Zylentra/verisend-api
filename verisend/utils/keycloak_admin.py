"""
Keycloak Admin API service for managing user roles and authentication.

Uses the forms-api service account credentials to assign/remove
realm roles from users, and to manage magic link authentication.
"""

import logging
import uuid
from typing import Annotated, Optional

import httpx
from fastapi import Depends
from keycloak import KeycloakAdmin, KeycloakOpenID, KeycloakOpenIDConnection

from verisend.settings import settings
from verisend.models.roles import Role

logger = logging.getLogger(__name__)


class KeycloakAdminService:
    """Service for managing users in Keycloak via Admin API"""

    def __init__(self):
        connection = KeycloakOpenIDConnection(
            server_url=settings.keycloak_server_url,
            realm_name=settings.keycloak_realm,
            client_id=settings.keycloak_client_id,
            client_secret_key=settings.keycloak_client_secret.get_secret_value(),
            verify=True,
        )
        self.admin = KeycloakAdmin(connection=connection)
        self.openid = KeycloakOpenID(
            server_url=settings.keycloak_server_url,
            realm_name=settings.keycloak_realm,
            client_id=settings.keycloak_client_id,
            client_secret_key=settings.keycloak_client_secret.get_secret_value(),
        )

    def assign_role(self, user_id: str, role: Role) -> None:
        """Assign a realm role to a user"""
        realm_role = self.admin.get_realm_role(role.value)
        self.admin.assign_realm_roles(user_id=user_id, roles=[realm_role])
        logger.info(f"Assigned role '{role.value}' to user {user_id}")

    def remove_role(self, user_id: str, role: Role) -> None:
        """Remove a realm role from a user"""
        realm_role = self.admin.get_realm_role(role.value)
        self.admin.delete_realm_roles_of_user(user_id=user_id, roles=[realm_role])
        logger.info(f"Removed role '{role.value}' from user {user_id}")

    def find_user_by_email(self, email: str) -> Optional[dict]:
        """Find a user by email address"""
        users = self.admin.get_users({"email": email})
        return users[0] if users else None

    def create_user(self, email: str) -> dict:
        """Create a new user with a UUID username"""
        user_id = self.admin.create_user({
            "username": str(uuid.uuid4()),
            "email": email,
            "enabled": True,
            "emailVerified": True,
        })
        return self.admin.get_user(user_id)

    def get_user_by_id(self, user_id: str) -> dict:
        """Get user by Keycloak ID"""
        return self.admin.get_user(user_id)

    def get_user_tokens(self, user_id: str) -> dict:
        """Get tokens for a user via token exchange (for magic link auth).

        Makes a direct HTTP call to the token endpoint because python-keycloak's
        exchange_token sends 'requested_subject' which requires fine-grained authz
        permissions that may not be available. This uses the standard token exchange
        grant with subject_token pointing to the service account and audience set
        to impersonate the target user.
        """
        service_token = self.openid.token(grant_type="client_credentials")

        token_url = (
            f"{settings.keycloak_server_url}/realms/{settings.keycloak_realm}"
            f"/protocol/openid-connect/token"
        )

        response = httpx.post(
            token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret.get_secret_value(),
                "subject_token": service_token["access_token"],
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "requested_subject": user_id,
                "requested_token_type": "urn:ietf:params:oauth:token-type:refresh_token",
                "scope": "openid",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            raise Exception(f"{response.status_code}: {response.text}")

        return response.json()


keycloak_admin_service = KeycloakAdminService()


def get_keycloak_admin() -> KeycloakAdminService:
    return keycloak_admin_service


KeycloakAdminDep = Annotated[KeycloakAdminService, Depends(get_keycloak_admin)]