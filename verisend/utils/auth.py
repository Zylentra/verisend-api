import hashlib
import warnings
from functools import lru_cache
from typing import Optional, Annotated
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.status import HTTP_403_FORBIDDEN, HTTP_401_UNAUTHORIZED
from fastapi.security.api_key import APIKeyHeader
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends
from pydantic import BaseModel, SecretStr, field_validator
from sqlmodel import select
import jwt
from verisend.models.roles import Role
from verisend.settings import settings
from verisend.models.authenticated_user import AuthenticatedUser


class FormsAuthenticationConfig(BaseModel):
    api_key: Optional[SecretStr] = None
    
    @field_validator("api_key")
    @classmethod
    def warn_api_key(cls, api_key: Optional[str]) -> Optional[str]:
        if api_key is None:
            warnings.warn("No api_key provided starting app unauthenticated.")
        return api_key

@lru_cache(maxsize=1)
def get_auth_settings():
    """Get authentication settings from application settings"""
    api_key = settings.api_key.get_secret_value() if hasattr(settings, 'api_key') else None
    return FormsAuthenticationConfig(api_key=api_key)

class UnauthorizedException(HTTPException):
    def __init__(self, detail: str, **kwargs):
        super().__init__(HTTP_403_FORBIDDEN, detail=detail)

class UnauthenticatedException(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=HTTP_401_UNAUTHORIZED, 
            detail="Requires authentication"
        )


class VerifyToken:
    def __init__(self):
        self.config = settings
        jwks_url = f'{self.config.keycloak_server_url}/realms/{self.config.keycloak_realm}/protocol/openid-connect/certs'
        self.jwks_client = jwt.PyJWKClient(jwks_url, headers={"User-Agent": "forms-api/1.0"})
    
    async def verify_bearer_token(self, token: str) -> dict:
        """Verify bearer token and return payload"""
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token).key
        except jwt.exceptions.PyJWKClientError as error:
            raise UnauthorizedException(str(error))
        except jwt.exceptions.DecodeError as error:
            raise UnauthorizedException(str(error))
        
        try:
            # Keycloak token validation
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience="account",
                issuer=f'{self.config.keycloak_server_url}/realms/{self.config.keycloak_realm}',
            )
        except Exception as error:
            raise UnauthorizedException(str(error))
        
        return payload

# Create the security schemes that will be visible in Swagger
api_key_header = APIKeyHeader(name='x-api-key', auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

class Authentication:
    """Authentication that supports both API key and bearer token"""
    
    def __init__(self):
        self.token_verifier = VerifyToken()
    
    async def __call__(
        self,
        request: Request,
        api_key: Optional[str] = Depends(api_key_header),
        bearer_token: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
    ) -> AuthenticatedUser:
        """
        Try API key authentication first, then bearer token.
        Returns AuthenticatedUser model or raises HTTPException.
        """
        auth_settings = get_auth_settings()

        # Try API key authentication first
        if api_key:
            # Check admin API key (from env)
            if auth_settings.api_key and api_key == auth_settings.api_key.get_secret_value():
                return AuthenticatedUser(
                    user_id="api-key-user",
                    auth_type="api_key",
                    authenticated=True,
                    role=Role.ADMIN,
                )

            # TODO: Use a cache/keystore (e.g. Redis or in memory keystore) instead of hitting the DB on every request...
            # Check org API keys (from DB)
            from verisend.models.db_models import OrgApiKey
            from verisend.utils.db import get_async_session

            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            async for session in get_async_session():
                result = await session.exec(
                    select(OrgApiKey).where(OrgApiKey.key_hash == key_hash)
                )
                org_key = result.first()

            if org_key:
                return AuthenticatedUser(
                    user_id=f"api-key-{org_key.id}",
                    auth_type="org_api_key",
                    authenticated=True,
                    role=Role.ORG_USER,
                    org_id=str(org_key.org_id),
                )

            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Invalid API key"
            )
        
        if bearer_token:
            try:
                payload = await self.token_verifier.verify_bearer_token(bearer_token.credentials)
                user_id = payload.get("sub")
                if not user_id:
                    raise HTTPException(
                        status_code=HTTP_401_UNAUTHORIZED,
                        detail="Token missing 'sub' claim"
                    )
                
                keycloak_roles = payload.get("realm_access", {}).get("roles", [])
                role = Role.from_keycloak_roles(keycloak_roles)
                
                return AuthenticatedUser(
                    user_id=user_id,
                    auth_type="bearer_token",
                    authenticated=True,
                    role=role,
                    email=payload.get("email"),
                    name=payload.get("name"),
                    payload=payload,
                    token=bearer_token.credentials
                )
            except (UnauthorizedException, UnauthenticatedException) as e:
                # Bearer token is invalid
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid bearer token: {str(e)}"
                )
        
        if auth_settings.api_key is None:
            return AuthenticatedUser(
                user_id="anonymous",
                auth_type="none",
                authenticated=False,
                role=Role.USER,
            )
        
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide either a valid API key (x-api-key header) or bearer token (Authorization: Bearer <token>)."
        )


authentication = Authentication()

Authenticated = Annotated[AuthenticatedUser, Depends(authentication)]


class RoleChecker:
    """Dependency that checks if user has the required role"""

    def __init__(self, role: Role):
        self.role = role

    def __call__(self, auth: AuthenticatedUser = Depends(authentication)) -> AuthenticatedUser:
        if auth.role != self.role:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail=f"Requires role: {self.role.value}"
            )
        return auth


RequireAdmin = Annotated[AuthenticatedUser, Depends(RoleChecker(Role.ADMIN))]
RequireOrgUser = Annotated[AuthenticatedUser, Depends(RoleChecker(Role.ORG_USER))]
RequireUser = Annotated[AuthenticatedUser, Depends(RoleChecker(Role.USER))]