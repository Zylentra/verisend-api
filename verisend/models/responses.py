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


class SubmitFormResponse(BaseModel):
    submission_id: UUID
    standard_fields_saved: int


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
    styling: StylingResponse | None
    sections: list[FillSectionResponse]
