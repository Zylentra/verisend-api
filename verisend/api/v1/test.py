from fastapi import APIRouter, UploadFile, status, File
from fastapi.responses import StreamingResponse
import json
from pydantic import BaseModel, Field
from pydantic_ai import Agent, AgentRunResultEvent, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, ThinkingPart, ThinkingPartDelta
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from verisend.utils.blob_storage import BlobStorageContainer
from verisend.utils.db import AsyncSession
from verisend.settings import settings
from verisend.utils.auth import RequirePublisher


TAGS = [
    {
        "name": "Test",
        "description": "Test endpoints",
    },
]

router = APIRouter(tags=["Test"], prefix="/test")

@router.post("/test-upload", status_code=status.HTTP_201_CREATED)
async def upload_setup(
    auth: RequirePublisher,
    session: AsyncSession,
    container: BlobStorageContainer,
    file: UploadFile = File(...),
):
    """Upload a document to create a new setup (publisher+ only)"""
    from uuid import uuid4
    from verisend.workers.tasks import extract_form

    content = await file.read()
    file_id = uuid4()

    blob_path = f"test/{file_id}/{file.filename}"
    blob_client = container.get_blob_client(blob_path)
    blob_client.upload_blob(content)

    url = blob_client.url
    print(f"Blob URL: {url}")

    setup_id = str(uuid4())
    extract_form.delay(setup_id, url)  # type: ignore

    return {"url": url, "file_id": str(file_id)}