import logging
from uuid import UUID, uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File, status
from sqlmodel import select

from verisend.utils.blob_storage import BlobStorageContainer
from verisend.utils.db import AsyncSession
from verisend.models.db_models import Form, FormSection, FormSubmission, JobStatus, ProcessingJob, UserStandardFieldValue
from verisend.utils.auth import Authenticated
from verisend.models.requests import ConfirmRequest, ExtractStylingRequest, SubmitFormRequest, StylingRequest, UpdateSectionsRequest
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
# Endpoints
# =============================================================================

@router.get("/active", response_model=list[FormListItem])
async def list_active_forms(session: AsyncSession):
    statement = (
        select(Form)
        .where(Form.is_active == True, Form.is_deleted == False)
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


# TODO: For the PoC this returns all active forms. Once FormAssignment is
# implemented, filter by forms assigned to the authenticated user. Kept as a
# separate endpoint so the frontend has a clear separation of concerns.
@router.get("/assigned", response_model=list[FormListItem])
async def list_assigned_forms(session: AsyncSession):
    statement = (
        select(Form)
        .where(Form.is_active == True, Form.is_deleted == False)
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
async def list_draft_forms(session: AsyncSession):
    statement = (
        select(Form)
        .where(Form.is_active == False, Form.is_deleted == False)
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


@router.post("/styling/extract", response_model=StylingResponse)
async def extract_styling(body: ExtractStylingRequest):
    try:
        extracted = await extract_styling_from_url(body.url, body.available_fonts)
        print(f"Extracted styling: {extracted}")
    except Exception as e:
        logger.exception("Styling extraction failed for URL: %s", body.url)
        raise HTTPException(status_code=422, detail=f"Could not extract styling: {e}")
    return StylingResponse(**extracted.model_dump())


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    container: BlobStorageContainer = ...,
    session: AsyncSession = ...,
):
    form_id = uuid4()
    now = datetime.now(timezone.utc)

    # Upload to blob first
    blob_path = f"forms/{form_id}/original/{file.filename}"
    blob_client = container.get_blob_client(blob_path)
    contents = await file.read()
    blob_client.upload_blob(contents, overwrite=True)
    pdf_url = blob_client.url

    # Summarise while we have the URL
    result = await summarise_form(pdf_url)

    form = Form(
        id=form_id,
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
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

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
    session: AsyncSession,
):
    statement = select(ProcessingJob).where(ProcessingJob.form_id == form_id)
    result = await session.exec(statement)
    job = result.first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_id = job.id
    job_status = job.status
    job_progress = job.progress
    job_step = job.current_step

    return JobStatusResponse(
        form_id=form_id,
        job_id=job_id,
        status=job_status,
        progress=job_progress,
        current_step=job_step,
    )


@router.get("/{form_id}/sections", response_model=FormSectionsResponse)
async def get_sections(
    form_id: UUID,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    form_name = form.name
    form_is_active = form.is_active

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
        name=form_name,
        is_active=form_is_active,
        sections=section_responses,
    )


@router.put("/{form_id}/sections", response_model=UpdateSectionsResponse)
async def update_sections(
    form_id: UUID,
    body: UpdateSectionsRequest,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    now = datetime.now(timezone.utc)

    # Delete existing sections for this form
    existing = await session.exec(
        select(FormSection).where(FormSection.form_id == form_id)
    )
    for old in existing.all():
        await session.delete(old)

    # Build new sections and capture response data before commit
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
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    form.styling = body.model_dump()
    form.updated_at = datetime.now(timezone.utc)
    session.add(form)
    await session.commit()

    return body


@router.get("/{form_id}/styling", response_model=StylingResponse)
async def get_styling(
    form_id: UUID,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    if not form.styling:
        raise HTTPException(status_code=404, detail="Styling not configured")

    return StylingResponse(**form.styling)


@router.post("/{form_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate(
    form_id: UUID,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    if form.is_active:
        raise HTTPException(status_code=400, detail="Form is already active")

    form.is_active = True
    form.updated_at = datetime.now(timezone.utc)
    session.add(form)
    await session.commit()


@router.get("/{form_id}/fill", response_model=FormFillResponse)
async def get_form_for_filling(
    form_id: UUID,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form or form.is_deleted:
        raise HTTPException(status_code=404, detail="Form not found")
    if not form.is_active:
        raise HTTPException(status_code=400, detail="Form is not active")

    # Load sections
    result = await session.exec(
        select(FormSection)
        .where(FormSection.form_id == form_id)
        .order_by(FormSection.section_number)
    )
    sections = result.all()

    # Build response — standard field values are handled client-side via the vault
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
    body: SubmitFormRequest,
    auth: Authenticated,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form or form.is_deleted:
        raise HTTPException(status_code=404, detail="Form not found")
    if not form.is_active:
        raise HTTPException(status_code=400, detail="Form is not active")

    now = datetime.now(timezone.utc)
    submission_id = uuid4()

    # Save the submission
    submission = FormSubmission(
        id=submission_id,
        form_id=form_id,
        user_id=auth.user_id,
        data=[f.model_dump() for f in body.fields],
        created_at=now,
    )
    session.add(submission)

    # Extract and upsert standard field values
    standard_fields = {
        f.standard_field_key: f.value
        for f in body.fields
        if f.standard_field_key and f.value
    }

    existing = await session.exec(
        select(UserStandardFieldValue).where(
            UserStandardFieldValue.user_id == auth.user_id,
            UserStandardFieldValue.standard_field_key.in_(list(standard_fields.keys())),  # type: ignore[union-attr]
        )
    )
    existing_by_key = {sfv.standard_field_key: sfv for sfv in existing.all()}

    for key, value in standard_fields.items():
        if key in existing_by_key:
            existing_by_key[key].value = value
            existing_by_key[key].updated_at = now
            session.add(existing_by_key[key])
        else:
            session.add(UserStandardFieldValue(
                user_id=auth.user_id,
                standard_field_key=key,
                value=value,
                updated_at=now,
            ))

    standard_fields_saved = len(standard_fields)

    await session.commit()

    return SubmitFormResponse(
        submission_id=submission_id,
        standard_fields_saved=standard_fields_saved,
    )


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    form_id: UUID,
    session: AsyncSession,
):
    form = await session.get(Form, form_id)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    form.is_deleted = True
    form.updated_at = datetime.now(timezone.utc)
    session.add(form)
    await session.commit()
