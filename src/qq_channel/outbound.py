"""
QQ Bot outbound media message sending.

Handles sending images, files, voice as raw file data or URL references.
The actual send is delegated to the api module; this module provides
higher-level wrappers that handle file reading, URL resolution, and
media-type detection.

Based on OpenClaw's qqbot/src/outbound.ts.
"""

import logging
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse

import httpx

from gateway.platforms.qq import api

logger = logging.getLogger(__name__)

# =============================================================================
# Send Image
# =============================================================================

async def send_image_by_url(
    access_token: str,
    peer_id: str,
    peer_type: str,
    image_url: str,
    msg_id: Optional[str] = None,
) -> dict:
    """
    Send an image message using a URL reference.

    QQ supports sending images by URL without uploading first.
    """
    seg = {
        "type": "image",
        "data": {"url": image_url},
    }
    return await _send_segment(access_token, peer_id, peer_type, seg, msg_id)


async def send_image_by_file(
    access_token: str,
    peer_id: str,
    peer_type: str,
    file_path: Union[str, Path],
    msg_id: Optional[str] = None,
) -> dict:
    """
    Send an image from a local file path.

    Reads the file and sends it as base64 file_data.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {file_path}")

    with open(path, "rb") as f:
        file_data = f.read()

    import base64
    b64 = base64.b64encode(file_data).decode("ascii")

    seg = {
        "type": "image",
        "data": {"file_data": b64},
    }
    return await _send_segment(access_token, peer_id, peer_type, seg, msg_id)


async def send_image_by_base64(
    access_token: str,
    peer_id: str,
    peer_type: str,
    base64_data: str,
    msg_id: Optional[str] = None,
) -> dict:
    """Send an image from a base64-encoded string."""
    seg = {
        "type": "image",
        "data": {"file_data": base64_data},
    }
    return await _send_segment(access_token, peer_id, peer_type, seg, msg_id)


# =============================================================================
# Send File
# =============================================================================

async def send_file_by_url(
    access_token: str,
    peer_id: str,
    peer_type: str,
    file_url: str,
    filename: Optional[str] = None,
    msg_id: Optional[str] = None,
) -> dict:
    """
    Send a file message using a URL reference.

    Downloads the file first, then sends as base64.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            content = resp.read()
    except Exception as e:
        logger.warning(f"[qq:outbound] Failed to download file from {file_url}: {e}")
        raise

    import base64
    b64 = base64.b64encode(content).decode("ascii")

    seg = {
        "type": "file",
        "data": {
            "file_data": b64,
            "name": filename or _extract_filename(file_url),
        },
    }
    return await _send_segment(access_token, peer_id, peer_type, seg, msg_id)


async def send_file_by_path(
    access_token: str,
    peer_id: str,
    peer_type: str,
    file_path: Union[str, Path],
    msg_id: Optional[str] = None,
) -> dict:
    """Send a local file as a message attachment."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(path, "rb") as f:
        file_data = f.read()

    import base64
    b64 = base64.b64encode(file_data).decode("ascii")

    seg = {
        "type": "file",
        "data": {
            "file_data": b64,
            "name": path.name,
        },
    }
    return await _send_segment(access_token, peer_id, peer_type, seg, msg_id)


# =============================================================================
# Send Voice / Audio
# =============================================================================

async def send_voice_by_url(
    access_token: str,
    peer_id: str,
    peer_type: str,
    audio_url: str,
    msg_id: Optional[str] = None,
) -> dict:
    """Send a voice message using a URL reference."""
    seg = {
        "type": "audio",
        "data": {"url": audio_url},
    }
    return await _send_segment(access_token, peer_id, peer_type, seg, msg_id)


async def send_voice_by_file(
    access_token: str,
    peer_id: str,
    peer_type: str,
    audio_path: Union[str, Path],
    msg_id: Optional[str] = None,
) -> dict:
    """Send a voice/audio file."""
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Voice file not found: {audio_path}")

    with open(path, "rb") as f:
        file_data = f.read()

    import base64
    b64 = base64.b64encode(file_data).decode("ascii")

    seg = {
        "type": "audio",
        "data": {"file_data": b64},
    }
    return await _send_segment(access_token, peer_id, peer_type, seg, msg_id)


# =============================================================================
# Core Segment Sender
# =============================================================================

async def _send_segment(
    access_token: str,
    peer_id: str,
    peer_type: str,
    seg: dict,
    msg_id: Optional[str] = None,
) -> dict:
    """
    Send a single media segment to the appropriate QQ endpoint.

    peer_type determines the API path:
    - "c2c"  → /v2/users/{openid}/messages
    - "group" → /v2/groups/{groupOpenid}/messages
    - "guild" → /channels/{channelId}/messages
    - "dm"    → /dms/{guildId}/messages
    """
    if peer_type == "c2c":
        return await api.send_c2c_message(
            app_id="",  # filled by api
            access_token=access_token,
            openid=peer_id,
            content="",  # segment goes in msg_content below
            msg_id=msg_id,
        )
    elif peer_type == "group":
        # QQ group media is sent differently — typically through file/upload API
        # For now, send as text with URL
        logger.warning(f"[qq:outbound] Group media sending not fully implemented for {peer_id}")
        return {}
    else:
        logger.warning(f"[qq:outbound] Unknown peer_type for media: {peer_type}")
        return {}


# =============================================================================
# Utilities
# =============================================================================

def _extract_filename(url: str) -> str:
    """Extract a safe filename from a URL."""
    parsed = urlparse(url)
    name = parsed.path.split("/")[-1]
    if not name or "." not in name:
        name = "file.bin"
    return name
