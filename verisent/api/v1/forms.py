import logging
from uuid import UUID, uuid4
from datetime import datetime, timezone

from azure.storage.blob import ContentSettings
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, status
from sqlmodel import select

from verisent.utils.blob_storage import BlobStorageContainer
from verisent.utils.db import AsyncSession
from verisent.utils.clerk import ClerkDep
from verisent.models.db_models import (
    Form, FormSection, FormSubmission, JobStatus,
    Organization, OrgMembership, ProcessingJob, User,
)
from verisent.settings import settings
from verisent.utils.auth import Authenticated, RequireOrgUser
from verisent.models.requests import AssignFormRequest, ConfirmRequest, ExtractStylingRequest, StylingRequest, UpdateSectionsRequest
from verisent.models.responses import (
    ConfirmResponse,
    FieldResponse,
    FillFieldResponse,
    FillSectionResponse,
    FormFillResponse,
    FormListItem,
    FormSectionsResponse,
    JobStatusResponse,
    LogoUploadResponse,
    SectionResponse,
    SubmissionDetailResponse,
    SubmissionListItem,
    SubmitFormResponse,
    StylingResponse,
    UpdateSectionsResponse,
    UploadResponse,
)
from verisent.agents.styling_agent import extract_styling_from_url
from verisent.agents.summarise_agent import summarise_form
from verisent.utils.email import send_form_assignment_email
from verisent.utils.logo import MAX_BYTES as LOGO_MAX_BYTES, store_logo_bytes
from verisent.utils.pdf import render_first_page_thumbnail_async
from verisent.workers.tasks import extract_form

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

async def _get_user_org(session: AsyncSession, user_id: str) -> OrgMembership:
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
    membership = await _get_user_org(session, auth.user_id)
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
            thumbnail_url=f.thumbnail_url,
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
    membership = await _get_user_org(session, auth.user_id)
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
            thumbnail_url=f.thumbnail_url,
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
            FormSubmission.user_id == auth.user_id,
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
            thumbnail_url=f.thumbnail_url,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in result.all()
    ]


@router.post("/styling/extract", response_model=StylingResponse)
async def extract_styling(
    body: ExtractStylingRequest,
    auth: RequireOrgUser,
    container: BlobStorageContainer,
):
    try:
        extracted = await extract_styling_from_url(body.url, body.available_fonts, container)
    except Exception as e:
        logger.exception("Styling extraction failed for URL: %s", body.url)
        raise HTTPException(status_code=422, detail=f"Could not extract styling: {e}")
    return StylingResponse(**extracted.model_dump())


@router.post("/styling/logo", response_model=LogoUploadResponse)
async def upload_styling_logo(
    auth: RequireOrgUser,
    container: BlobStorageContainer,
    file: UploadFile = File(...),
):
    """Upload a brand logo file. Returns the public blob URL."""
    data = await file.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Logo exceeds {LOGO_MAX_BYTES} byte cap",
        )
    try:
        logo_url = await store_logo_bytes(data, container)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return LogoUploadResponse(logo_url=logo_url)


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload(
    auth: RequireOrgUser,
    session: AsyncSession,
    container: BlobStorageContainer,
    file: UploadFile = File(...),
):
    membership = await _get_user_org(session, auth.user_id)

    form_id = uuid4()
    now = datetime.now(timezone.utc)

    # Upload to blob
    blob_path = f"forms/{form_id}/original/{file.filename}"
    blob_client = container.get_blob_client(blob_path)
    contents = await file.read()
    blob_client.upload_blob(contents, overwrite=True)
    pdf_url = blob_client.url

    # Render + upload a first-page thumbnail (best-effort).
    thumbnail_url: str | None = None
    try:
        thumb_bytes = await render_first_page_thumbnail_async(contents)
        thumb_blob = container.get_blob_client(f"forms/{form_id}/thumbnail.jpg")
        thumb_blob.upload_blob(
            thumb_bytes,
            overwrite=True,
            content_settings=ContentSettings(content_type="image/jpeg"),
        )
        thumbnail_url = thumb_blob.url
    except Exception:
        logger.exception("Failed to render/upload thumbnail for form %s", form_id)

    # Summarise
    result = await summarise_form(pdf_url)

    form = Form(
        id=form_id,
        org_id=membership.org_id,
        name=result.name,
        original_filename=file.filename or "",
        pdf_url=pdf_url,
        thumbnail_url=thumbnail_url,
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
    membership = await _get_user_org(session, auth.user_id)
    form = await _get_org_form(session, form_id, membership.org_id)

    now = datetime.now(timezone.utc)
    job_id = uuid4()
    pdf_url = form.pdf_url

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

    extract_form.delay(str(job_id), str(form_id), pdf_url, body.summary, body.context)

    return ConfirmResponse(form_id=form_id, job_id=job_id)


@router.get("/{form_id}/status", response_model=JobStatusResponse)
async def get_status(
    form_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    membership = await _get_user_org(session, auth.user_id)
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
    membership = await _get_user_org(session, auth.user_id)
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
    membership = await _get_user_org(session, auth.user_id)
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
    membership = await _get_user_org(session, auth.user_id)
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
    membership = await _get_user_org(session, auth.user_id)
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
    membership = await _get_user_org(session, auth.user_id)
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
    membership = await _get_user_org(session, auth.user_id)
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
    clerk: ClerkDep,
):
    """Assign a form to a user by email. Creates the user if they don't exist."""
    membership = await _get_user_org(session, auth.user_id)
    form = await _get_org_form(session, form_id, membership.org_id)

    if not form.is_active:
        raise HTTPException(status_code=400, detail="Form must be active before assigning")

    email = body.email.lower()

    # Find or create user in Clerk
    clerk_user = clerk.find_user_by_email(email)
    is_new_clerk_user = clerk_user is None
    if not clerk_user:
        clerk_user = clerk.create_user(email)

    clerk_user_id = clerk_user["id"]

    # Ensure local user record exists
    user = await session.get(User, clerk_user_id)
    if not user:
        user = User(id=clerk_user_id, email=email)
        session.add(user)

    # Check not already assigned (pending)
    existing = await session.exec(
        select(FormSubmission).where(
            FormSubmission.form_id == form_id,
            FormSubmission.user_id == clerk_user_id,
            FormSubmission.completed_at == None,
        )
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="Form is already assigned to this user")

    submission_id = uuid4()
    submission = FormSubmission(
        id=submission_id,
        form_id=form_id,
        user_id=clerk_user_id,
    )
    session.add(submission)

    # Resolve everything we need for the email while the ORM objects are still fresh.
    org = await session.get(Organization, membership.org_id)
    org_name = org.name if org else None
    form_name = form.name

    await session.commit()

    # For brand-new users, Clerk invitation sends a sign-up link to the app home.
    if is_new_clerk_user:
        try:
            clerk.create_invitation(email, redirect_url=settings.app_url)
        except Exception:
            logger.exception("Failed to send Clerk invitation to %s", email)

    # Always send our own assignment notification so existing users know too.
    try:
        await send_form_assignment_email(
            to_email=email,
            form_name=form_name,
            org_name=org_name,
        )
    except Exception:
        logger.exception("Failed to send assignment email to %s", email)

    return {"submission_id": str(submission_id), "email": email}


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

    user_id = auth.user_id
    now = datetime.now(timezone.utc)

    data = await request.body()

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

    blob_path = f"submissions/{submission_id}/data.json"
    blob_client = container.get_blob_client(blob_path)
    blob_client.upload_blob(data, overwrite=True)

    submission.data_url = blob_client.url
    submission.completed_at = now
    session.add(submission)
    await session.commit()

    return SubmitFormResponse(submission_id=submission_id)


# =============================================================================
# Submission viewing (org users)
# =============================================================================

@router.get("/{form_id}/submissions", response_model=list[SubmissionListItem])
async def list_submissions(
    form_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """List all submissions for a form. Org members only."""
    membership = await _get_user_org(session, auth.user_id)
    await _get_org_form(session, form_id, membership.org_id)

    result = await session.exec(
        select(FormSubmission, User)
        .join(User)
        .where(FormSubmission.form_id == form_id)
        .order_by(FormSubmission.created_at.desc())
    )

    return [
        SubmissionListItem(
            submission_id=sub.id,
            user_id=user.id,
            email=user.email,
            data_url=sub.data_url,
            completed_at=sub.completed_at,
            created_at=sub.created_at,
        )
        for sub, user in result.all()
    ]


@router.get("/{form_id}/submissions/{submission_id}", response_model=SubmissionDetailResponse)
async def get_submission(
    form_id: UUID,
    submission_id: UUID,
    auth: RequireOrgUser,
    session: AsyncSession,
):
    """Get a single submission. Org members only."""
    membership = await _get_user_org(session, auth.user_id)
    await _get_org_form(session, form_id, membership.org_id)

    submission = await session.get(FormSubmission, submission_id)
    if not submission or submission.form_id != form_id:
        raise HTTPException(status_code=404, detail="Submission not found")

    user = await session.get(User, submission.user_id)

    return SubmissionDetailResponse(
        submission_id=submission.id,
        form_id=submission.form_id,
        user_id=submission.user_id,
        email=user.email if user else "",
        data_url=submission.data_url,
        completed_at=submission.completed_at,
        created_at=submission.created_at,
    )
