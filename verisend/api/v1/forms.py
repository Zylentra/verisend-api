import asyncio
import logging
from uuid import UUID, uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, status
from sqlmodel import select

from verisend.utils.blob_storage import BlobStorageContainer
from verisend.utils.db import AsyncSession
from verisend.utils.keycloak_admin import KeycloakAdminDep
from verisend.models.db_models import (
    Form, FormSection, FormSubmission, JobStatus,
    Organization, OrgMembership, ProcessingJob, User,
)
from verisend.utils.auth import Authenticated, RequireOrgUser
from verisend.models.requests import AssignFormRequest, ConfirmRequest, ExtractStylingRequest, StylingRequest, UpdateSectionsRequest
from verisend.models.responses import (
    ConfirmResponse,
    FieldResponse,
    FillFieldResponse,
    FillSectionResponse,
    FormFillResponse,
    FormListItem,
    FormSectionsResponse,
    JobStatusResponse,
    SectionResponse,
    SubmitFormResponse,
    StylingResponse,
    UpdateSectionsResponse,
    UploadResponse,
)
from verisend.agents.styling_agent import extract_styling_from_url
from verisend.agents.summarise_agent import summarise_form
from verisend.workers.tasks import extract_form

logger = logging.getLogger(__name__)

TAGS = [
    {
        "name": "Forms",
        "description": "Endpoints for managing forms",
    },
]

router = APIRouter(prefix="/forms", tags=["forms"])


# =============================================================================
# Helpers
# =============================================================================

async def _get_user_org(session: AsyncSession, user_id: UUID) -> OrgMembership:
    """Get the user's org membership. Raises 403 if not in any org."""
    result = await session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user_id)
    )
    membership = result.first()
    if not membership:
        raise HTTPException(status_code=403, detail="User is not a member of any organization")
    return membership


async def _get_org_form(session: AsyncSession, form_id: UUID, org_id: UUID) -> Form:
    """Get a form, verifying it belongs to the org and is not deleted."""
    form = await session.get(Form, form_id)
    if not form or form.is_deleted or form.org_id != org_id:
        raise HTTPException(status_code=404, detail="Form not found")
    return form


# =============================================================================
# Org user endpoints (form management)
# =============================================================================

@router.get("/active", response_model=list[FormListItem])
async def list_active_forms(
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """List active forms for the authenticated user's org."""
    membership = await _get_user_org(session, UUID(auth.user_id))
    statement = (
        select(Form)
        .where(Form.org_id == membership.org_id, Form.is_active == True, Form.is_deleted == False)
        .order_by(Form.updated_at.desc())
    )
    result = await session.exec(statement)
    return [
        FormListItem(
            form_id=f.id,
            name=f.name,
            original_filename=f.original_filename,
            is_active=f.is_active,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in result.all()
    ]


@router.get("/drafts", response_model=list[FormListItem])
async def list_draft_forms(
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """List draft forms for the authenticated user's org."""
    membership = await _get_user_org(session, UUID(auth.user_id))
    statement = (
        select(Form)
        .where(Form.org_id == membership.org_id, Form.is_active == False, Form.is_deleted == False)
        .order_by(Form.updated_at.desc())
    )
    result = await session.exec(statement)
    return [
        FormListItem(
            form_id=f.id,
            name=f.name,
            original_filename=f.original_filename,
            is_active=f.is_active,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in result.all()
    ]


@router.get("/assigned", response_model=list[FormListItem])
async def list_assigned_forms(
    auth: Authenticated,
    session: AsyncSession,
):
    """List forms assigned to the authenticated user (pending submissions)."""
    result = await session.exec(
        select(Form)
        .join(FormSubmission)
        .where(
            FormSubmission.user_id == UUID(auth.user_id),
            FormSubmission.completed_at == None,
            Form.is_active == True,
            Form.is_deleted == False,
        )
        .order_by(Form.updated_at.desc())
    )
    return [
        FormListItem(
            form_id=f.id,
            name=f.name,
            original_filename=f.original_filename,
            is_active=f.is_active,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in result.all()
    ]


@router.post("/styling/extract", response_model=StylingResponse)
async def extract_styling(body: ExtractStylingRequest, auth: RequireOrgUser):
    try:
        extracted = await extract_styling_from_url(body.url, body.available_fonts)
    except Exception as e:
        logger.exception("Styling extraction failed for URL: %s", body.url)
        raise HTTPException(status_code=422, detail=f"Could not extract styling: {e}")
    return StylingResponse(**extracted.model_dump())


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload(
    auth: RequireOrgUser,
    session: AsyncSession,
    container: BlobStorageContainer,
    file: UploadFile = File(...),
):
    membership = await _get_user_org(session, UUID(auth.user_id))

    form_id = uuid4()
    now = datetime.now(timezone.utc)

    # Upload to blob
    blob_path = f"forms/{form_id}/original/{file.filename}"
    blob_client = container.get_blob_client(blob_path)
    contents = await file.read()
    blob_client.upload_blob(contents, overwrite=True)
    pdf_url = blob_client.url

    # Summarise
    result = await summarise_form(pdf_url)

    form = Form(
        id=form_id,
        org_id=membership.org_id,
        name=result.name,
        original_filename=file.filename or "",
        pdf_url=pdf_url,
        summary=result.summary,
        created_at=now,
        updated_at=now,
    )
    session.add(form)
    await session.commit()

    return UploadResponse(
        form_id=form_id,
        pdf_url=pdf_url,
        name=result.name,
        summary=result.summary,
    )


@router.post("/{form_id}/confirm", response_model=ConfirmResponse)
async def confirm(
    form_id: UUID,
    body: ConfirmRequest,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    now = datetime.now(timezone.utc)
    job_id = uuid4()

    form.name = body.name
    form.summary = body.summary
    form.context = body.context
    form.updated_at = now

    job = ProcessingJob(
        id=job_id,
        form_id=form_id,
        status=JobStatus.PENDING.value,
        progress=0,
        created_at=now,
        updated_at=now,
    )
    session.add(form)
    session.add(job)
    await session.commit()

    extract_form.delay(str(job_id), str(form_id), form.pdf_url, body.summary, body.context)

    return ConfirmResponse(form_id=form_id, job_id=job_id)


@router.get("/{form_id}/status", response_model=JobStatusResponse)
async def get_status(
    form_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    await _get_org_form(session, form_id, membership.org_id)

    statement = select(ProcessingJob).where(ProcessingJob.form_id == form_id)
    result = await session.exec(statement)
    job = result.first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        form_id=form_id,
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        current_step=job.current_step,
    )


@router.get("/{form_id}/sections", response_model=FormSectionsResponse)
async def get_sections(
    form_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    statement = (
        select(FormSection)
        .where(FormSection.form_id == form_id)
        .order_by(FormSection.section_number)
    )
    result = await session.exec(statement)
    sections = result.all()

    section_responses = []
    for s in sections:
        raw_fields = s.fields or []
        fields = [
            FieldResponse(
                label=f.get("label", ""),
                field_type=f.get("field_type", "short_text"),
                required=f.get("required", False),
                placeholder=f.get("placeholder"),
                help_text=f.get("help_text"),
                options=f.get("options"),
                standard_field_key=f.get("standard_field_key"),
                standard_field_reason=f.get("standard_field_reason"),
            )
            for f in raw_fields
        ]
        section_responses.append(
            SectionResponse(
                id=s.id,
                section_number=s.section_number,
                name=s.name,
                description=s.description,
                page_start=s.page_start,
                page_end=s.page_end,
                fields=fields,
            )
        )

    return FormSectionsResponse(
        form_id=form_id,
        name=form.name,
        is_active=form.is_active,
        sections=section_responses,
    )


@router.put("/{form_id}/sections", response_model=UpdateSectionsResponse)
async def update_sections(
    form_id: UUID,
    body: UpdateSectionsRequest,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    now = datetime.now(timezone.utc)

    existing = await session.exec(
        select(FormSection).where(FormSection.form_id == form_id)
    )
    for old in existing.all():
        await session.delete(old)

    section_responses: list[SectionResponse] = []
    for s in body.sections:
        section_id = s.id or uuid4()
        field_dicts = [f.model_dump() for f in s.fields]

        section = FormSection(
            id=section_id,
            form_id=form_id,
            section_number=s.section_number,
            name=s.name,
            description=s.description,
            page_start=s.page_start,
            page_end=s.page_end,
            fields=field_dicts,
            created_at=now,
            updated_at=now,
        )
        session.add(section)

        section_responses.append(
            SectionResponse(
                id=section_id,
                section_number=s.section_number,
                name=s.name,
                description=s.description,
                page_start=s.page_start,
                page_end=s.page_end,
                fields=[FieldResponse(**f) for f in field_dicts],
            )
        )

    form.updated_at = now
    session.add(form)
    await session.commit()

    return UpdateSectionsResponse(sections=section_responses)


@router.put("/{form_id}/styling", response_model=StylingResponse)
async def update_styling(
    form_id: UUID,
    body: StylingRequest,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    form.styling = body.model_dump()
    form.updated_at = datetime.now(timezone.utc)
    session.add(form)
    await session.commit()

    return body


@router.get("/{form_id}/styling", response_model=StylingResponse)
async def get_styling(
    form_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    if not form.styling:
        raise HTTPException(status_code=404, detail="Styling not configured")

    return StylingResponse(**form.styling)


@router.post("/{form_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate(
    form_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    if form.is_active:
        raise HTTPException(status_code=400, detail="Form is already active")

    form.is_active = True
    form.updated_at = datetime.now(timezone.utc)
    session.add(form)
    await session.commit()


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    form_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    form.is_deleted = True
    form.updated_at = datetime.now(timezone.utc)
    session.add(form)
    await session.commit()


@router.post("/{form_id}/assign", status_code=status.HTTP_201_CREATED)
async def assign_form(
    form_id: UUID,
    body: AssignFormRequest,
    auth: RequireOrgUser,
    session: AsyncSession,
    keycloak: KeycloakAdminDep,
):
    """Assign a form to a user by email. Creates the user if they don't exist."""
    membership = await _get_user_org(session, UUID(auth.user_id))
    form = await _get_org_form(session, form_id, membership.org_id)

    if not form.is_active:
        raise HTTPException(status_code=400, detail="Form must be active before assigning")

    email = body.email.lower()

    # Find or create user in Keycloak
    kc_user = await asyncio.to_thread(keycloak.find_user_by_email, email)
    if not kc_user:
        kc_user = await asyncio.to_thread(keycloak.create_user, email)

    kc_user_id = UUID(kc_user["id"])

    # Ensure local user record exists
    user = await session.get(User, kc_user_id)
    if not user:
        user = User(id=kc_user_id, email=email)
        session.add(user)

    # Check not already assigned (pending)
    existing = await session.exec(
        select(FormSubmission).where(
            FormSubmission.form_id == form_id,
            FormSubmission.user_id == kc_user_id,
            FormSubmission.completed_at == None,
        )
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="Form is already assigned to this user")

    submission = FormSubmission(
        form_id=form_id,
        user_id=kc_user_id,
    )
    session.add(submission)
    await session.commit()

    return {"submission_id": str(submission.id), "email": email}


# =============================================================================
# Standard user endpoints (form filling)
# =============================================================================

@router.get("/{form_id}/fill", response_model=FormFillResponse)
async def get_form_for_filling(
    form_id: UUID,
    auth: Authenticated,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form or form.is_deleted:
        raise HTTPException(status_code=404, detail="Form not found")
    if not form.is_active:
        raise HTTPException(status_code=400, detail="Form is not active")

    # Get the org's public key for encryption
    org = await session.get(Organization, form.org_id)
    if not org or not org.public_key:
        raise HTTPException(status_code=500, detail="Organization encryption not configured")

    result = await session.exec(
        select(FormSection)
        .where(FormSection.form_id == form_id)
        .order_by(FormSection.section_number)
    )
    sections = result.all()

    styling = StylingResponse(**form.styling) if form.styling else None

    section_responses = []
    for s in sections:
        fields = []
        for f in (s.fields or []):
            fields.append(FillFieldResponse(
                label=f.get("label", ""),
                field_type=f.get("field_type", "short_text"),
                required=f.get("required", False),
                placeholder=f.get("placeholder"),
                help_text=f.get("help_text"),
                options=f.get("options"),
                standard_field_key=f.get("standard_field_key"),
            ))

        section_responses.append(FillSectionResponse(
            id=s.id,
            section_number=s.section_number,
            name=s.name,
            description=s.description,
            fields=fields,
        ))

    return FormFillResponse(
        form_id=form_id,
        name=form.name,
        summary=form.summary,
        public_key=org.public_key,
        styling=styling,
        sections=section_responses,
    )


@router.post("/{form_id}/submit", response_model=SubmitFormResponse, status_code=status.HTTP_201_CREATED)
async def submit_form(
    form_id: UUID,
    request: Request,
    auth: Authenticated,
    session: AsyncSession,
    container: BlobStorageContainer,
):
    form = await session.get(Form, form_id)
    if not form or form.is_deleted:
        raise HTTPException(status_code=404, detail="Form not found")
    if not form.is_active:
        raise HTTPException(status_code=400, detail="Form is not active")

    user_id = UUID(auth.user_id)
    now = datetime.now(timezone.utc)

    # Read encrypted payload from request body
    encrypted_data = await request.body()

    # Check for existing pending submission (assignment)
    result = await session.exec(
        select(FormSubmission).where(
            FormSubmission.form_id == form_id,
            FormSubmission.user_id == user_id,
            FormSubmission.completed_at == None,
        )
    )
    submission = result.first()

    if submission:
        submission_id = submission.id
    else:
        submission_id = uuid4()
        submission = FormSubmission(
            id=submission_id,
            form_id=form_id,
            user_id=user_id,
            created_at=now,
        )

    # Upload encrypted blob
    blob_path = f"submissions/{submission_id}/data.enc"
    blob_client = container.get_blob_client(blob_path)
    blob_client.upload_blob(encrypted_data, overwrite=True)

    submission.data_url = blob_client.url
    submission.completed_at = now
    session.add(submission)
    await session.commit()

    return SubmitFormResponse(submission_id=submission_id)
