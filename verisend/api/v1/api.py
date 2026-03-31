import hashlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from verisend.models.db_models import Form, FormSubmission, OrgApiKey, User
from verisend.models.responses import (
    ApiSubmissionDetailResponse,
    ApiSubmissionListItem,
    ApiSubmissionsListResponse,
)
from verisend.utils.auth import Authenticated
from verisend.utils.db import AsyncSession


TAGS = [
    {
        "name": "API",
        "description": "External API endpoints (org API key auth)",
    },
]

router = APIRouter(prefix="/api", tags=["API"])


async def _get_api_key(session: AsyncSession, auth: Authenticated) -> OrgApiKey:
    """Get the OrgApiKey record for the authenticated API key."""
    if auth.auth_type != "org_api_key":
        raise HTTPException(status_code=403, detail="This endpoint requires an org API key")

    # Extract the key ID from user_id (format: "api-key-{uuid}")
    key_id = auth.user_id.replace("api-key-", "")
    api_key = await session.get(OrgApiKey, UUID(key_id))
    if not api_key:
        raise HTTPException(status_code=403, detail="API key not found")
    return api_key


@router.get("/submissions", response_model=ApiSubmissionsListResponse)
async def list_submissions(
    auth: Authenticated,
    session: AsyncSession,
    form_id: UUID | None = None,
):
    """List completed submissions for the org. Optionally filter by form_id."""
    api_key = await _get_api_key(session, auth)

    query = (
        select(FormSubmission, Form, User)
        .join(Form)
        .join(User)
        .where(
            Form.org_id == api_key.org_id,
            FormSubmission.completed_at != None,
        )
    )

    if form_id:
        query = query.where(FormSubmission.form_id == form_id)

    query = query.order_by(FormSubmission.completed_at.desc())
    result = await session.exec(query)

    submissions = [
        ApiSubmissionListItem(
            submission_id=sub.id,
            form_id=sub.form_id,
            form_name=form.name,
            user_id=user.id,
            email=user.email,
            data_url=sub.data_url,
            completed_at=sub.completed_at,
            created_at=sub.created_at,
        )
        for sub, form, user in result.all()
    ]

    return ApiSubmissionsListResponse(
        submissions=submissions,
        encrypted_private_key=api_key.encrypted_private_key,
        encrypted_org_private_key=api_key.encrypted_org_private_key,
    )


@router.get("/submissions/{submission_id}", response_model=ApiSubmissionDetailResponse)
async def get_submission(
    submission_id: UUID,
    auth: Authenticated,
    session: AsyncSession,
):
    """Get a single submission with decryption keys."""
    api_key = await _get_api_key(session, auth)

    submission = await session.get(FormSubmission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Verify submission belongs to this org
    form = await session.get(Form, submission.form_id)
    if not form or form.org_id != api_key.org_id:
        raise HTTPException(status_code=404, detail="Submission not found")

    user = await session.get(User, submission.user_id)

    return ApiSubmissionDetailResponse(
        submission_id=submission.id,
        form_id=submission.form_id,
        form_name=form.name,
        user_id=submission.user_id,
        email=user.email if user else "",
        data_url=submission.data_url,
        completed_at=submission.completed_at,
        created_at=submission.created_at,
        encrypted_private_key=api_key.encrypted_private_key,
        encrypted_org_private_key=api_key.encrypted_org_private_key,
    )
