import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from verisend.utils.auth import Authenticated
from verisend.utils.blob_storage import BlobStorageContainer

logger = logging.getLogger(__name__)

TAGS = [
    {
        "name": "Vault",
        "description": "Encrypted user vault storage",
    },
]

router = APIRouter(prefix="/vault", tags=["vault"])


def _vault_path(user_id: str) -> str:
    return f"vaults/{user_id}/vault.enc"


@router.put("", status_code=status.HTTP_204_NO_CONTENT)
async def save_vault(
    request: Request,
    auth: Authenticated,
    container: BlobStorageContainer,
):
    body = await request.body()
    blob_client = container.get_blob_client(_vault_path(auth.user_id))
    blob_client.upload_blob(body, overwrite=True)


@router.get("")
async def get_vault(
    auth: Authenticated,
    container: BlobStorageContainer,
):
    blob_client = container.get_blob_client(_vault_path(auth.user_id))

    if not blob_client.exists():
        raise HTTPException(status_code=404, detail="No vault found")

    data = blob_client.download_blob().readall()
    return Response(content=data, media_type="application/octet-stream")
