from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    ORG_USER = "org_user"
    USER = "user"

    @classmethod
    def from_keycloak_roles(cls, roles: list[str]) -> "Role":
        """Get highest privilege role from Keycloak role list"""
        if "admin" in roles:
            return cls.ADMIN
        if "org_user" in roles:
            return cls.ORG_USER
        return cls.USER
