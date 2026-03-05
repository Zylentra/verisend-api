from typing import Annotated, Generator
from azure.storage.blob import BlobServiceClient, ContainerClient
from contextlib import contextmanager
from fastapi import Depends
from verisend.settings import settings


@contextmanager
def get_blob_storage_client() -> Generator[BlobServiceClient, None, None]:
    client = BlobServiceClient.from_connection_string(
        settings.blob_storage_connection_string.get_secret_value()
    )
    try:
        yield client
    finally:
        client.close()


def get_blob_container() -> Generator[ContainerClient, None, None]:
    with get_blob_storage_client() as client:
        container = client.get_container_client(settings.blob_storage_container_name)
        if not container.exists():
            container.create_container()
        yield container


BlobStorageContainer = Annotated[ContainerClient, Depends(get_blob_container)]