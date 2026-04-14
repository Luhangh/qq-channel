"""
QQ Bot reply text delivery with Markdown support.

Handles text and Markdown content delivery, optional streaming mode,
and routing to the appropriate API endpoint based on peer type.

Based on OpenClaw's qqbot/src/outbound-deliver.ts.
"""

import asyncio
import logging
import re
import time
import uuid
import httpx
from typing import Optional

from gateway.platforms.qq import api as qq_api

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 5000
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?\n]) +")


# =============================================================================
# Text / Markdown Delivery
# =============================================================================

async def send_text_reply(
    access_token: str,
    peer_id: str,
    peer_type: str,
    content: str,
    use_markdown: bool = False,
    msg_id: Optional[str] = None,
    message_reference: Optional[str] = None,
) -> dict:
    """
    Send a text or Markdown reply to a QQ user or group.

    Routes to the correct API endpoint based on peer_type.
    Truncates or splits content that exceeds MAX_CONTENT_LENGTH.
    """
    if len(content) > MAX_CONTENT_LENGTH:
        content = content[:MAX_CONTENT_LENGTH] + "\n[消息过长已截断]"

    msg_seq = _next_msg_seq()

    if use_markdown:
        body = {
            "markdown": {"content": content},
            "msg_type": 2,
            "msg_seq": msg_seq,
        }
    else:
        body = {
            "content": content,
            "msg_type": 0,
            "msg_seq": msg_seq,
        }

    if msg_id:
        body["msg_id"] = msg_id
    if message_reference and not use_markdown:
        body["message_reference"] = {"message_id": message_reference}

    try:
        if peer_type == "c2c":
            result = await qq_api.send_c2c_message(
                app_id="",
                access_token=access_token,
                openid=peer_id,
                content=content,
                msg_id=msg_id,
                message_reference=message_reference,
                use_markdown=use_markdown,
            )
        elif peer_type == "group":
            result = await qq_api.send_group_message(
                app_id="",
                access_token=access_token,
                group_openid=peer_id,
                content=content,
                msg_id=msg_id,
                use_markdown=use_markdown,
            )
        elif peer_type == "guild":
            result = await qq_api.send_channel_message(
                access_token=access_token,
                channel_id=peer_id,
                content=content,
                msg_id=msg_id,
            )
        elif peer_type == "dm":
            result = await qq_api.send_dm_message(
                access_token=access_token,
                guild_id=peer_id,
                content=content,
                msg_id=msg_id,
            )
        else:
            logger.error(f"[qq:deliver] Unknown peer_type: {peer_type}")
            return {}

        logger.info(f"[qq:deliver] Sent to {peer_type}/{peer_id}: {content[:60]}...")
        return result

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            logger.warning(f"[qq:deliver] 400 on send, trying chunked: {e}")
            return await _send_chunked(access_token, peer_id, peer_type, content, use_markdown)
        raise


async def _send_chunked(
    access_token: str,
    peer_id: str,
    peer_type: str,
    content: str,
    use_markdown: bool,
) -> dict:
    """Split long content at sentence boundaries and send sequentially."""
    chunks = _split_into_chunks(content)
    last_result = {}
    for chunk in chunks:
        last_result = await send_text_reply(
            access_token, peer_id, peer_type, chunk, use_markdown
        )
        await asyncio.sleep(0.3)
    return last_result


def _split_into_chunks(text: str, max_len: int = MAX_CONTENT_LENGTH) -> list[str]:
    """Split text at sentence boundaries, each part ≤ max_len."""
    parts = SENTENCE_SPLIT_RE.split(text)
    chunks = []
    current = ""

    for part in parts:
        if len(current) + len(part) + 1 <= max_len:
            current = (current + " " + part).strip()
        else:
            if current:
                chunks.append(current)
            current = part
            while len(current) > max_len:
                chunks.append(current[:max_len])
                current = current[max_len:]

    if current:
        chunks.append(current)

    return chunks or [text[:max_len]]


# =============================================================================
# Streaming (partial message updates)
# =============================================================================

# stream_id → {peer_id, peer_type, partial_content}
_stream_state: dict[str, dict] = {}


async def send_streaming_begin(
    access_token: str,
    peer_id: str,
    peer_type: str,
    msg_id: Optional[str] = None,
) -> Optional[str]:
    """
    Begin a streaming message with a "..." placeholder.

    Returns stream_id (the QQ msg_id) if successful.
    """
    msg_seq = _next_msg_seq()

    try:
        if peer_type == "c2c":
            result = await qq_api.send_c2c_message(
                app_id="",
                access_token=access_token,
                openid=peer_id,
                content="⏳ ...",
                msg_id=msg_id,
            )
        elif peer_type == "group":
            result = await qq_api.send_group_message(
                app_id="",
                access_token=access_token,
                group_openid=peer_id,
                content="⏳ ...",
                msg_id=msg_id,
            )
        else:
            return None

        stream_id = (
            result.get("data", {}).get("msg_id")
            or result.get("msg_id")
        )
        if stream_id:
            _stream_state[stream_id] = {
                "peer_id": peer_id,
                "peer_type": peer_type,
                "partial": "⏳ ...",
            }
            return stream_id
    except Exception as e:
        logger.warning(f"[qq:deliver] Streaming begin failed: {e}")

    return None


async def send_streaming_end(
    access_token: str,
    stream_id: str,
    final_text: str,
    use_markdown: bool = False,
) -> None:
    """
    End a streaming session, replacing the placeholder with final content.
    """
    state = _stream_state.pop(stream_id, None)
    if not state:
        return

    await send_text_reply(
        access_token=access_token,
        peer_id=state["peer_id"],
        peer_type=state["peer_type"],
        content=final_text,
        use_markdown=use_markdown,
    )


# =============================================================================
# Utilities
# =============================================================================

def _next_msg_seq() -> int:
    """Generate a message sequence number in 0..65535 range."""
    time_part = int(time.time() * 1000) % 100_000_000
    random_part = uuid.uuid4().int % 65536
    return (time_part ^ random_part) % 65536
