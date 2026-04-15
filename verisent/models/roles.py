from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    ORG_USER = "org_user"
    USER = "user"

    @classmethod
    def from_clerk_claims(cls, payload: dict) -> "Role":
        """Determine role from Clerk JWT claims.

        The 'role' claim comes from user.public_metadata.role,
        configured via Clerk's session token template.
        """
        role = payload.get("role", "user")
        if role == "admin":
            return cls.ADMIN
        if role == "org_user":
            return cls.ORG_USER
        return cls.USER
