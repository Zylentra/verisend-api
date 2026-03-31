from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from verisend.models.requests import StylingRequest


class SendMagicLinkResponse(BaseModel):
    message: str
    email: str


class UserInfo(BaseModel):
    id: str
    email: str
    first_name: str | None = ""
    last_name: str | None = ""


class VerifyTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserInfo


class UploadResponse(BaseModel):
    form_id: UUID
    pdf_url: str
    name: str
    summary: str


class ConfirmResponse(BaseModel):
    form_id: UUID
    job_id: UUID


class JobStatusResponse(BaseModel):
    form_id: UUID
    job_id: UUID
    status: str
    progress: int
    current_step: str | None


class FieldResponse(BaseModel):
    label: str
    field_type: str
    required: bool
    placeholder: str | None
    help_text: str | None
    options: list[str] | None
    standard_field_key: str | None
    standard_field_reason: str | None


class SectionResponse(BaseModel):
    id: UUID
    section_number: int
    name: str
    description: str | None
    page_start: int
    page_end: int
    fields: list[FieldResponse]


class FormSectionsResponse(BaseModel):
    form_id: UUID
    name: str
    is_active: bool
    sections: list[SectionResponse]


class UpdateSectionsResponse(BaseModel):
    sections: list[SectionResponse]


class FormListItem(BaseModel):
    form_id: UUID
    name: str
    original_filename: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class StandardFieldResponse(BaseModel):
    id: UUID
    key: str
    label: str
    field_type: str
    group: str | None
    default_options: list[str] | None
    description: str | None


class OrgResponse(BaseModel):
    id: UUID
    name: str
    business_name: str | None
    registration_number: str | None
    address: str
    owner_id: UUID
    public_key: str | None
    created_at: datetime


class OrgMemberResponse(BaseModel):
    user_id: UUID
    email: str
    has_public_key: bool
    has_key_grant: bool
    created_at: datetime


class KeyGrantResponse(BaseModel):
    org_id: UUID
    user_id: UUID
    created_at: datetime


class OrgApiKeyResponse(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    created_at: datetime


class OrgApiKeyCreatedResponse(OrgApiKeyResponse):
    """Returned only at creation time — includes the raw API key (shown once)."""
    api_key: str


class KeypairStatusResponse(BaseModel):
    has_keypair: bool
    public_key: str | None


class SubmitFormResponse(BaseModel):
    submission_id: UUID


class StylingResponse(StylingRequest):
    pass


class FillFieldResponse(BaseModel):
    label: str
    field_type: str
    required: bool
    placeholder: str | None
    help_text: str | None
    options: list[str] | None
    standard_field_key: str | None


class FillSectionResponse(BaseModel):
    id: UUID
    section_number: int
    name: str
    description: str | None
    fields: list[FillFieldResponse]


class FormFillResponse(BaseModel):
    form_id: UUID
    name: str
    summary: str | None
    public_key: str
    styling: StylingResponse | None
    sections: list[FillSectionResponse]
