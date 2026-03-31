from uuid import UUID
from typing import Literal

from pydantic import BaseModel, EmailStr


class SendMagicLinkRequest(BaseModel):
    email: EmailStr


class ConfirmRequest(BaseModel):
    name: str
    summary: str | None = None
    context: str | None = None


class FieldInput(BaseModel):
    label: str
    field_type: str
    required: bool = False
    placeholder: str | None = None
    help_text: str | None = None
    options: list[str] | None = None
    standard_field_key: str | None = None
    standard_field_reason: str | None = None


class SectionInput(BaseModel):
    id: UUID | None = None
    section_number: int
    name: str
    description: str | None = None
    page_start: int
    page_end: int
    fields: list[FieldInput]


class UpdateSectionsRequest(BaseModel):
    sections: list[SectionInput]


class ExtractStylingRequest(BaseModel):
    url: str
    available_fonts: dict[str, str]


class FieldSubmission(BaseModel):
    label: str
    field_type: str
    standard_field_key: str | None = None
    value: str | None = None


class StandardFieldRequest(BaseModel):
    key: str
    label: str
    field_type: str
    group: str | None = None
    default_options: list[str] | None = None
    description: str | None = None


class UpdateStandardFieldRequest(BaseModel):
    label: str | None = None
    group: str | None = None
    default_options: list[str] | None = None
    description: str | None = None


class CreateOrgRequest(BaseModel):
    name: str
    business_name: str | None = None
    registration_number: str | None = None
    address: str
    public_key: str
    encrypted_org_private_key: str  # encrypted with the owner's public key


class InviteMemberRequest(BaseModel):
    email: EmailStr


class CreateKeyGrantRequest(BaseModel):
    encrypted_org_private_key: str  # encrypted with the member's public key


class AssignFormRequest(BaseModel):
    email: EmailStr


class SetupKeypairRequest(BaseModel):
    public_key: str
    encrypted_private_key: str


class CreateOrgApiKeyRequest(BaseModel):
    name: str
    public_key: str
    encrypted_private_key: str
    encrypted_org_private_key: str


class StylingRequest(BaseModel):
    primary_color: str
    accent_color: str
    background_color: str
    surface_color: str
    text_color: str
    label_color: str
    border_color: str
    error_color: str
    font_family: str
    heading_size: Literal["sm", "md", "lg"]
    border_radius: Literal["none", "sm", "md", "lg", "full"]
    spacing: Literal["compact", "comfortable", "spacious"]
    button_style: Literal["filled", "outlined"]
    logo_url: str | None = None
