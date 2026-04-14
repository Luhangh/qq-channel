"""
Utility functions for QQ Bot adapter.

Text parsing, CQ code escaping, file path resolution, etc.
"""

import re
import logging
import uuid
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# =============================================================================
# CQ Code Escaping (for sending)
# =============================================================================

def escape_cq_code(text: str) -> str:
    """
    Escape special characters in message content for CQ code format.

    CQ special chars: [ ] ( ) , &
    """
    return (
        text.replace("&", "&amp;")
            .replace("[", "&#91;")
            .replace("]", "&#93;")
    )


def unescape_cq_code(text: str) -> str:
    """Reverse CQ code escaping."""
    return (
        text.replace("&#91;", "[")
            .replace("&#93;", "]")
            .replace("&amp;", "&")
    )


# =============================================================================
# Text Extraction from QQ Message Content
# =============================================================================

def extract_text_from_content(content) -> str:
    """
    Extract plain text from QQ message content.

    QQ messages can be:
    - A plain string (pure text)
    - A list of segments: [{"type": "text", "data": {"text": "..."}}, ...]

    Returns the extracted text as a string.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for seg in content:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return "".join(parts)

    # Fallback
    return str(content)


def extract_attachments_from_content(content) -> list:
    """
    Extract attachment segments from QQ message content.

    Returns list of segments that are NOT text (images, files, etc.)
    """
    if not isinstance(content, list):
        return []

    attachments = []
    for seg in content:
        if isinstance(seg, dict) and seg.get("type") != "text":
            attachments.append(seg)
    return attachments


# =============================================================================
# Message Segment Building (for sending)
# =============================================================================

def build_text_segment(text: str) -> dict:
    """Build a text message segment for QQ API."""
    return {
        "type": "text",
        "data": {"text": text}
    }


def build_image_segment(file_data: Optional[str] = None, url: Optional[str] = None) -> dict:
    """Build an image message segment for QQ API."""
    if file_data:
        return {"type": "image", "data": {"file_data": file_data}}
    elif url:
        return {"type": "image", "data": {"url": url}}
    else:
        raise ValueError("Must provide either file_data or url for image segment")


# =============================================================================
# File & Path Utilities
# =============================================================================

def get_qqbot_data_dir(*subdirs: str) -> Path:
    """
    Return the QQ bot data directory: {HERMES_HOME}/qqbot/

    Creates the directory if it doesn't exist.
    """
    from hermes_constants import get_hermes_dir
    base = get_hermes_dir("qqbot", "qqbot")
    full = base.joinpath(*subdirs)
    full.mkdir(parents=True, exist_ok=True)
    return full


def encode_account_id_for_filename(account_id: str) -> str:
    """Encode account ID to a safe filename (base64url)."""
    import base64
    return base64.urlsafe_b64encode(account_id.encode("utf-8")).decode("ascii").rstrip("=")


def sanitize_filename(name: str) -> str:
    """Remove path separators and null bytes from a filename."""
    return name.replace("\x00", "").replace("/", "_").replace("\\", "_").strip(". ")


# =============================================================================
# Misc
# =============================================================================

def get_file_extension(url: str) -> str:
    """Extract file extension from URL."""
    parsed = url.split("?")[0]
    ext = os.path.splitext(parsed)[1].lower()
    return ext if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp3", ".silk", ".ogg", ".amr"} else ".bin"


def guess_mime_type(url: str, content_type: str = "") -> str:
    """Guess MIME type from URL extension or content-type header."""
    if content_type:
        return content_type.split(";")[0].strip()
    ext = get_file_extension(url)
    MIME_MAP = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp3": "audio/mpeg",
        ".silk": "audio/silk",
        ".ogg": "audio/ogg",
        ".amr": "audio/amr",
    }
    return MIME_MAP.get(ext, "application/octet-stream")
