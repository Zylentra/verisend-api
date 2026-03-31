import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from verisend.models.db_models import User
from verisend.models.requests import SetupKeypairRequest
from verisend.models.responses import KeypairStatusResponse
from verisend.utils.auth import Authenticated
from verisend.utils.blob_storage import BlobStorageContainer
from verisend.utils.db import AsyncSession

logger = logging.getLogger(__name__)

TAGS = [
    {
        "name": "Users",
        "description": "User profile, keypair, and vault endpoints",
    },
]

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/me/keypair", status_code=status.HTTP_204_NO_CONTENT)
async def setup_keypair(
    body: SetupKeypairRequest,
    auth: Authenticated,
    session: AsyncSession,
):
    """Save the user's public key and encrypted private key. Called once during vault setup."""
    user = await session.get(User, UUID(auth.user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.public_key is not None:
        raise HTTPException(status_code=409, detail="Keypair already set up")

    user.public_key = body.public_key
    user.encrypted_private_key = body.encrypted_private_key
    session.add(user)
    await session.commit()


@router.get("/me/keypair", response_model=KeypairStatusResponse)
async def get_keypair_status(
    auth: Authenticated,
    session: AsyncSession,
):
    """Check if the user has set up their keypair."""
    user = await session.get(User, UUID(auth.user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return KeypairStatusResponse(
        has_keypair=user.public_key is not None,
        public_key=user.public_key,
    )


# ---- Vault ----


def _vault_path(user_id: str) -> str:
    return f"vaults/{user_id}/vault.enc"


@router.put("/me/vault", status_code=status.HTTP_204_NO_CONTENT)
async def save_vault(
    request: Request,
    auth: Authenticated,
    container: BlobStorageContainer,
):
    body = await request.body()
    blob_client = container.get_blob_client(_vault_path(auth.user_id))
    blob_client.upload_blob(body, overwrite=True)


@router.get("/me/vault")
async def get_vault(
    auth: Authenticated,
    container: BlobStorageContainer,
):
    blob_client = container.get_blob_client(_vault_path(auth.user_id))

    if not blob_client.exists():
        raise HTTPException(status_code=404, detail="No vault found")

    data = blob_client.download_blob().readall()
    return Response(content=data, media_type="application/octet-stream")
