"""
QQ Bot reply dispatcher — routes Hermes responses back to QQ.

Handles structured payloads (image+text combos), streaming responses,
and automatic retry on token expiry.

Based on OpenClaw's qqbot/src/reply-dispatcher.ts.
"""

import asyncio
import logging
from typing import Optional, Literal, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# =============================================================================
# Structured Reply Payload
# =============================================================================

@dataclass
class ReplyPayload:
    """
    Normalized reply payload produced by Hermes after processing a message.

    Attributes:
        text: Plain text or Markdown content.
        image_urls: Optional list of image URLs to include.
        audio_url: Optional voice/audio URL.
        file_url: Optional file attachment URL.
        stream_mode: None, "partial", or "complete".
        use_markdown: Whether to send as Markdown (msg_type=2).
        quoted_message_id: Optional QQ message_id to quote/reply to.
    """
    text: str
    image_urls: Optional[list[str]] = None
    audio_url: Optional[str] = None
    file_url: Optional[str] = None
    stream_mode: Optional[Literal["partial", "complete"]] = None
    use_markdown: bool = False
    quoted_message_id: Optional[str] = None


# =============================================================================
# Reply Dispatcher
# =============================================================================

class ReplyDispatcher:
    """
    Routes Hermes reply payloads to the appropriate QQ sending function.

    Handles:
    - Plain text replies (with Markdown support)
    - Image + text combinations
    - Streaming partial → complete transitions
    - Automatic token refresh on 401
    """

    def __init__(
        self,
        account_config: "QQBotAccountConfig",
        token_getter,  # async fn(app_id, client_secret) -> access_token
        get_api_base: Optional[callable] = None,
    ):
        """
        Args:
            account_config: QQBotAccountConfig with app_id, client_secret, etc.
            token_getter: Async callable that returns a fresh access_token.
        """
        from gateway.platforms.qq.config import QQBotAccountConfig
        from gateway.platforms.qq import outbound_deliver as deliver

        self._account = account_config
        self._get_token = token_getter
        self._get_api_base = get_api_base or (lambda: "")
        self._deliver = deliver

        # Per-peer streaming state: peer_key → stream_id
        self._streams: dict[str, str] = {}

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def send_reply(
        self,
        peer_id: str,
        peer_type: str,
        payload: ReplyPayload,
    ) -> dict:
        """
        Send a complete reply payload to the given peer.

        Handles image+text combos by sending media first, then text.
        """
        if not payload.text and not payload.image_urls and not payload.audio_url and not payload.file_url:
            logger.debug(f"[qq:dispatcher] Empty payload for {peer_type}/{peer_id}, skipping")
            return {}

        try:
            access_token = await self._get_token(
                self._account.app_id,
                self._account.client_secret,
            )
        except Exception as e:
            logger.error(f"[qq:dispatcher] Failed to get access token: {e}")
            return {"error": str(e)}

        try:
            # Handle streaming mode
            if payload.stream_mode == "partial":
                return await self._send_partial(
                    access_token, peer_id, peer_type, payload
                )
            elif payload.stream_mode == "complete":
                return await self._send_complete(
                    access_token, peer_id, peer_type, payload
                )

            # Send media attachments first
            if payload.image_urls:
                await self._send_images(
                    access_token, peer_id, peer_type, payload.image_urls
                )
            if payload.file_url:
                await self._send_file(
                    access_token, peer_id, peer_type, payload.file_url
                )

            # Send text (or Markdown)
            if payload.text:
                result = await self._deliver.send_text_reply(
                    access_token=access_token,
                    peer_id=peer_id,
                    peer_type=peer_type,
                    content=payload.text,
                    use_markdown=payload.use_markdown,
                    message_reference=payload.quoted_message_id,
                )
                return result

            return {}

        except Exception as e:
            logger.error(f"[qq:dispatcher] Send failed for {peer_type}/{peer_id}: {e}")
            return {"error": str(e)}

    async def send_text_only(
        self,
        peer_id: str,
        peer_type: str,
        text: str,
        use_markdown: bool = False,
        quoted_message_id: Optional[str] = None,
    ) -> dict:
        """Convenience: send plain text without building a ReplyPayload."""
        return await self.send_reply(
            peer_id=peer_id,
            peer_type=peer_type,
            payload=ReplyPayload(
                text=text,
                use_markdown=use_markdown,
                quoted_message_id=quoted_message_id,
            ),
        )

    async def send_typing_indicator(
        self,
        peer_id: str,
        peer_type: str,
        seconds: int = 60,
    ) -> None:
        """Send a typing indicator (input notify) to a C2C chat."""
        from gateway.platforms.qq import api as qq_api

        try:
            access_token = await self._get_token(
                self._account.app_id,
                self._account.client_secret,
            )
            if peer_type == "c2c":
                await qq_api.send_c2c_input_notify(
                    access_token=access_token,
                    openid=peer_id,
                    msg_id=None,
                    input_second=seconds,
                )
        except Exception as e:
            logger.debug(f"[qq:dispatcher] Typing indicator failed (non-fatal): {e}")

    # -------------------------------------------------------------------------
    # Streaming
    # -------------------------------------------------------------------------

    async def _send_partial(
        self,
        access_token: str,
        peer_id: str,
        peer_type: str,
        payload: ReplyPayload,
    ) -> dict:
        """Send a streaming-partial reply (placeholder message)."""
        stream_key = f"{peer_type}:{peer_id}"
        existing = self._streams.get(stream_key)

        if existing:
            # Append to existing stream
            stream_id = existing
        else:
            # Start new stream
            stream_id = await self._deliver.send_streaming_begin(
                access_token, peer_id, peer_type
            )
            if stream_id:
                self._streams[stream_key] = stream_id

        return {"stream_id": stream_id}

    async def _send_complete(
        self,
        access_token: str,
        peer_id: str,
        peer_type: str,
        payload: ReplyPayload,
    ) -> dict:
        """End a streaming session with the final content."""
        stream_key = f"{peer_type}:{peer_id}"
        stream_id = self._streams.pop(stream_key, None)

        result = await self._deliver.send_streaming_end(
            access_token=access_token,
            stream_id=stream_id or "",
            final_text=payload.text,
            use_markdown=payload.use_markdown,
        )
        return result or {}

    # -------------------------------------------------------------------------
    # Media Helpers
    # -------------------------------------------------------------------------

    async def _send_images(
        self,
        access_token: str,
        peer_id: str,
        peer_type: str,
        image_urls: list[str],
    ) -> None:
        """Send one or more images."""
        from gateway.platforms.qq import outbound

        for url in image_urls:
            try:
                await outbound.send_image_by_url(
                    access_token=access_token,
                    peer_id=peer_id,
                    peer_type=peer_type,
                    image_url=url,
                )
                await asyncio.sleep(0.2)  # Brief delay between images
            except Exception as e:
                logger.warning(f"[qq:dispatcher] Image send failed {url}: {e}")

    async def _send_file(
        self,
        access_token: str,
        peer_id: str,
        peer_type: str,
        file_url: str,
    ) -> None:
        """Send a file attachment."""
        from gateway.platforms.qq import outbound

        try:
            await outbound.send_file_by_url(
                access_token=access_token,
                peer_id=peer_id,
                peer_type=peer_type,
                file_url=file_url,
            )
        except Exception as e:
            logger.warning(f"[qq:dispatcher] File send failed {file_url}: {e}")
