"""Download, validate, and store brand logos in blob storage."""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from azure.storage.blob import ContainerClient, ContentSettings

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 10.0

_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
]


def _sniff_image(data: bytes) -> tuple[str, str]:
    """Return (mime, ext) by inspecting magic bytes. Raises ValueError if unrecognised."""
    for magic, mime, ext in _MAGIC:
        if data.startswith(magic):
            return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise ValueError("Unsupported image format")


async def _resolve_and_check_host(host: str) -> None:
    """Resolve host and reject if any address is private/loopback/link-local/etc."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve host: {e}") from e

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"Refusing to fetch from non-public address: {addr}")


async def _download_image(url: str) -> bytes:
    """Fetch an image with SSRF checks and a hard size cap. Returns raw bytes."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("URL is missing a host")

    await _resolve_and_check_host(parsed.hostname)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DOWNLOAD_TIMEOUT_S,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            buf = bytearray()
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise ValueError(f"Image exceeds {MAX_BYTES} byte cap")
            return bytes(buf)


async def store_logo_bytes(data: bytes, container: ContainerClient) -> str:
    """Validate image bytes, upload to blob storage, return the blob URL."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"Image exceeds {MAX_BYTES} byte cap")
    mime, ext = _sniff_image(data)

    blob_path = f"styling/logos/{uuid4()}.{ext}"
    blob_client = container.get_blob_client(blob_path)
    await asyncio.to_thread(
        blob_client.upload_blob,
        data,
        overwrite=True,
        content_settings=ContentSettings(content_type=mime),
    )
    return blob_client.url


async def download_and_store_logo(
    logo_url: str,
    base_url: str,
    container: ContainerClient,
) -> str:
    """
    Resolve `logo_url` against `base_url`, download it safely, validate it's an image,
    and upload it to blob storage. Returns the blob URL.

    Raises ValueError on anything suspicious. Callers should log + drop the logo field,
    not fail the whole styling extraction.
    """
    absolute = urljoin(base_url, logo_url)
    data = await _download_image(absolute)
    return await store_logo_bytes(data, container)
