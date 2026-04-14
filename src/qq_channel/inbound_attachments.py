"""
Processing of incoming rich-media attachments from QQ messages.

Downloads and caches images, voice, files. Extracts ASR text from voice.

Based on OpenClaw's qqbot/src/inbound-attachments.ts.
"""

import asyncio
import hashlib
import logging
import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from gateway.platforms.qq.types import MessageAttachment
from gateway.platforms.qq.utils import (
    get_qqbot_data_dir,
    guess_mime_type,
    get_file_extension,
)

logger = logging.getLogger(__name__)

# HTTP client shared across all attachment downloads
_http_client: Optional[httpx.AsyncClient] = None


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


# =============================================================================
# Attachment Download & Cache
# =============================================================================

async def download_to_cache(url: str, expected_ext: str = "") -> Optional[Path]:
    """
    Download a URL to the Hermes attachment cache.

    Returns the local Path, or None on failure.
    Cache key is the URL hash so duplicate downloads are avoided.
    """
    try:
        # Parse URL to get extension
        if not expected_ext:
            path_url = urlparse(url).path
            expected_ext = get_file_extension(path_url)

        # Build cache path: {qqbot_data_dir}/cache/{hash}.{ext}
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        cache_dir = get_qqbot_data_dir("cache")
        cache_path = cache_dir / f"{url_hash}{expected_ext}"

        # Return cached file if it exists
        if cache_path.exists():
            logger.debug(f"[qq:attach] Using cached file: {cache_path.name}")
            return cache_path

        # Download
        client = await _get_http_client()
        resp = await client.get(url)
        resp.raise_for_status()
        content = resp.read()

        # Write to cache
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(content)

        logger.info(f"[qq:attach] Downloaded {url[:80]} -> {cache_path.name} ({len(content)} bytes)")
        return cache_path

    except Exception as e:
        logger.warning(f"[qq:attach] Download failed for {url[:80]}: {e}")
        return None


# =============================================================================
# Image Processing
# =============================================================================

async def process_image_attachment(seg: dict, access_token: str) -> dict:
    """
    Process an incoming image attachment segment.

    Downloads the image, caches it, and returns enriched segment data
    with local file path.

    QQ image segments look like:
    {"type": "image", "data": {"file_size": 12345, "url": "https://..."}}
    """
    data = seg.get("data", {})
    url = data.get("url", "")

    if not url:
        return seg

    # Download to cache
    cached_path = await download_to_cache(url, ".jpg")
    if cached_path:
        # Enrich segment with local path
        data["local_path"] = str(cached_path)
        data["cached"] = True
    else:
        data["cached"] = False

    return seg


# =============================================================================
# Voice / Audio Processing
# =============================================================================

async def process_voice_attachment(
    seg: dict,
    access_token: str,
    app_id: str,
) -> dict:
    """
    Process an incoming voice attachment segment.

    Downloads the .silk/.wav file, converts if needed (future),
    and extracts ASR (Automatic Speech Recognition) text if available.

    QQ voice segments look like:
    {
      "type": "audio",
      "data": {
        "file_size": 12345,
        "duration": 5000,
        "url": "https://...",
        "voice_wav_url": "https://..."   # WAV version for ASR
      }
    }

    Returns the segment enriched with `asr_text` if available.
    """
    data = seg.get("data", {})
    wav_url = data.get("voice_wav_url") or data.get("url", "")

    if not wav_url:
        return seg

    # Download WAV to cache
    cached_path = await download_to_cache(wav_url, ".wav")
    if cached_path:
        data["local_path"] = str(cached_path)
        data["cached"] = True

        # TODO: Call ASR service here (e.g. Tencent ASR API)
        # For now, leave asr_text empty — will be filled in future
        # data["asr_text"] = await _do_asr(cached_path, access_token, app_id)

    return seg


# =============================================================================
# File Attachment Processing
# =============================================================================

async def process_file_attachment(seg: dict, access_token: str) -> dict:
    """
    Process an incoming file attachment segment.

    QQ file segments look like:
    {"type": "file", "data": {"file_size": 12345, "url": "...", "name": "document.pdf"}}
    """
    data = seg.get("data", {})
    url = data.get("url", "")
    filename = data.get("name", "file.bin")

    if not url:
        return seg

    # Determine extension from filename
    ext = Path(filename).suffix.lower()
    if not ext or ext == ".bin":
        ext = guess_mime_type(url, "").replace("application/", ".")

    cached_path = await download_to_cache(url, ext)
    if cached_path:
        data["local_path"] = str(cached_path)
        data["cached"] = True

    return seg


# =============================================================================
# Bulk Processing
# =============================================================================

async def process_attachments(
    segments: list,
    access_token: str,
    app_id: str,
) -> list:
    """
    Process all non-text segments from an incoming message concurrently.

    Downloads images, voice, files, and extracts ASR text from voice.
    Returns the enriched segments list.
    """
    tasks = []
    processed = list(segments)  # shallow copy

    for i, seg in enumerate(segments):
        seg_type = seg.get("type", "") if isinstance(seg, dict) else ""

        if seg_type == "image":
            tasks.append(_process_one(i, seg, "image", access_token, app_id))
        elif seg_type == "audio":
            tasks.append(_process_one(i, seg, "audio", access_token, app_id))
        elif seg_type == "file":
            tasks.append(_process_one(i, seg, "file", access_token, app_id))
        # text segments are passed through unchanged

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"[qq:attach] Attachment processing error: {result}")
            elif isinstance(result, tuple):
                idx, enriched = result
                if idx < len(processed):
                    processed[idx] = enriched

    return processed


async def _process_one(
    idx: int,
    seg: dict,
    seg_type: str,
    access_token: str,
    app_id: str,
) -> tuple[int, dict]:
    """Process a single attachment segment."""
    if seg_type == "image":
        enriched = await process_image_attachment(seg, access_token)
    elif seg_type == "audio":
        enriched = await process_voice_attachment(seg, access_token, app_id)
    elif seg_type == "file":
        enriched = await process_file_attachment(seg, access_token)
    else:
        enriched = seg
    return idx, enriched


# =============================================================================
# Format Text with Voice ASR
# =============================================================================

def format_voice_asr_text(segments: list) -> str:
    """
    If a message contains voice with ASR text, prepend it to the text content.

    Used when the bot needs to display or process voice message content.
    """
    text_parts = []
    asr_parts = []

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type", "")
        data = seg.get("data", {})

        if seg_type == "text":
            text_parts.append(data.get("text", ""))
        elif seg_type == "audio":
            asr = data.get("asr_refer_text") or data.get("asr_text") or ""
            if asr:
                asr_parts.append(f"[语音: {asr}]")

    result = ""
    if asr_parts:
        result += " ".join(asr_parts) + "\n"
    if text_parts:
        result += "".join(text_parts)

    return result.strip()
