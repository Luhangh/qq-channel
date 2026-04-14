"""
QQ Bot WebSocket gateway adapter.

Implements the QQ Open Platform WebSocket long-connection protocol for
receiving events, with automatic reconnect and session resume support.

Based on OpenClaw's qqbot/src/gateway.ts, translated to Python + asyncio websockets.
"""

import asyncio
import json
import logging
import signal
import sys
import time
import uuid
from typing import Optional, Callable, Awaitable, Any

import websockets
import httpx

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.platforms.qq import api as qq_api
from gateway.platforms.qq.types import (
    WSPayload, Intents, NormalizedQQMessage,
    C2CMessageEvent, GroupMessageEvent, GuildMessageEvent,
)
from gateway.platforms.qq.config import QQBotAccountConfig, resolve_qq_account_config
from gateway.platforms.qq.session_store import SessionState, load_session, save_session, clear_session
from gateway.platforms.qq.message_queue import MessageQueue, QueuedMessage
from gateway.platforms.qq.reply_dispatcher import ReplyDispatcher, ReplyPayload
from gateway.platforms.qq.utils import extract_text_from_content, escape_cq_code

logger = logging.getLogger(__name__)

# WebSocket reconnect delays in ms (exponential backoff)
RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000, 60000]
MAX_RECONNECT_ATTEMPTS = 100
QUICK_DISCONNECT_THRESHOLD_MS = 5000
MAX_QUICK_DISCONNECTS = 3
SESSION_EXPIRE_MS = 5 * 60 * 1000  # 5 minutes
PLUGIN_USER_AGENT = "QQBotPlugin/1.0 (Python)"

# Typing indicator duration in seconds
TYPING_INPUT_SECOND = 60


# =============================================================================
# QQ Adapter
# =============================================================================

class QQAdapter(BasePlatformAdapter):
    """
    Hermes Gateway adapter for QQ Open Platform bots.

    Uses WebSocket long-connection to receive events from QQ servers,
    and HTTP API to send messages back.

    Implements BasePlatformAdapter's required interface while matching
    the OpenClaw qqbot gateway.ts architecture.
    """

    MAX_MESSAGE_LENGTH = 5000

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.QQ)
        self._account: Optional[QQBotAccountConfig] = None
        self._access_token: Optional[str] = None
        self._gateway_url: Optional[str] = None
        self._ws: Optional[Any] = None  # websockets.WebSocketClientProtocol

        # Reconnect state
        self._reconnect_attempts = 0
        self._is_connecting = False
        self._is_running = False
        self._should_refresh_token = False
        self._last_connect_time: float = 0.0
        self._quick_disconnect_count = 0

        # Session state (for resume)
        self._session_id: Optional[str] = None
        self._last_seq: Optional[int] = None

        # Workers
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._msg_queue: Optional[MessageQueue] = None
        self._dispatcher: Optional[ReplyDispatcher] = None

        # Config
        self._account = resolve_qq_account_config(config.extra or {})
        if not self._account:
            raise ValueError("QQ Bot not configured: missing app_id")

        logger.info(
            f"[qq] QQ Bot configured: app_id={self._account.app_id}, "
            f"secret_source={self._account.secret_source}"
        )

    # -------------------------------------------------------------------------
    # BasePlatformAdapter required methods
    # -------------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """Send a message to a QQ user or group."""
        metadata = metadata or {}
        return await self.send_message(
            chat_id=chat_id,
            text=content,
            reply_to=reply_to,
            **metadata,
        )

    async def get_chat_info(self, chat_id: str) -> dict:
        """
        Get info about a QQ chat.

        chat_id format:
        - Numeric openid → C2C user info
        - "group:{group_openid}" → group info
        - "channel:{channel_id}" → channel info

        Returns a dict with at least: {name, type, chat_id}
        """
        peer_type, peer_id = self._parse_peer(chat_id)
        return {
            "name": f"QQ {peer_type}/{peer_id}",
            "type": peer_type,
            "chat_id": chat_id,
        }

    def platform(self) -> Platform:
        return Platform.QQ

    async def connect(self) -> bool:
        """Start the QQ Bot gateway — connect WebSocket and launch workers."""
        if self._is_running:
            return True

        account = self._account
        if not account:
            return False

        try:
            # Resolve token
            if not account.client_secret:
                logger.error("[qq] No client_secret configured — cannot connect")
                return False

            self._access_token = await qq_api.get_access_token(
                account.app_id, account.client_secret
            )
            logger.info(f"[qq] Access token obtained for app_id={account.app_id}")

            # Get gateway URL
            self._gateway_url = await qq_api.get_gateway_url(self._access_token)

            # Try to restore a saved session (for resume)
            saved = load_session(account.account_id, account.app_id)
            if saved:
                self._session_id = saved.session_id
                self._last_seq = saved.last_seq
                logger.info(
                    f"[qq] Restored session: sessionId={self._session_id}, lastSeq={self._last_seq}"
                )

            # Set up message queue and dispatcher
            self._msg_queue = MessageQueue(on_send=self._do_send_message)
            await self._msg_queue.start(concurrency=3)

            self._dispatcher = ReplyDispatcher(
                account_config=account,
                token_getter=qq_api.get_access_token,
            )

            # Connect WebSocket
            connected = await self._ws_connect()
            if not connected:
                return False

            self._is_running = True
            self._mark_connected()
            logger.info(f"[qq] Gateway connected: {self._account.app_id}")
            return True

        except Exception as e:
            logger.error(f"[qq] Failed to connect: {e}")
            await self._cleanup()
            return False

    async def disconnect(self) -> None:
        """Gracefully stop the gateway."""
        if not self._is_running:
            return

        self._is_running = False
        logger.info("[qq] Disconnecting...")

        # Cancel workers
        for task in [self._heartbeat_task, self._receive_task]:
            if task:
                task.cancel()

        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # Stop queue
        if self._msg_queue:
            await self._msg_queue.stop(timeout=5.0)

        await self._cleanup()
        self._mark_disconnected()
        logger.info("[qq] Disconnected")

    async def send_message(
        self,
        chat_id: str,
        text: str,
        **kwargs
    ) -> SendResult:
        """
        Send a message to a QQ user or group.

        chat_id format determines routing:
        - Numeric string → openid (C2C private)
        - prefixed: "group:{group_openid}" → group message
                   "channel:{channel_id}" → guild channel message
                   "dm:{guild_id}" → guild DM
        """
        # Parse peer_type from chat_id prefix or infer from format
        peer_type, peer_id = self._parse_peer(chat_id)

        try:
            # Get token
            token = await qq_api.get_access_token(
                self._account.app_id, self._account.client_secret
            )

            # Send via dispatcher
            result = await self._dispatcher.send_reply(
                peer_id=peer_id,
                peer_type=peer_type,
                payload=ReplyPayload(
                    text=text,
                    use_markdown=self._account.markdown_support,
                ),
            )

            if result and result.get("error"):
                return SendResult(success=False, error=result["error"])

            msg_id = str(result.get("data", {}).get("msg_id", result.get("msg_id", "")))
            return SendResult(success=True, message_id=msg_id or None)

        except Exception as e:
            logger.error(f"[qq] send_message failed: {e}")
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str, **kwargs) -> None:
        """Send typing indicator (C2C only)."""
        peer_type, peer_id = self._parse_peer(chat_id)
        if peer_type != "c2c":
            return

        try:
            token = await qq_api.get_access_token(
                self._account.app_id, self._account.client_secret
            )
            await qq_api.send_c2c_input_notify(
                access_token=token,
                openid=peer_id,
                msg_id=None,
                input_second=TYPING_INPUT_SECOND,
            )
        except Exception as e:
            logger.debug(f"[qq] Typing indicator failed (non-fatal): {e}")

    def _parse_peer(self, chat_id: str) -> tuple[str, str]:
        """Parse peer type and ID from chat_id string."""
        if chat_id.startswith("group:"):
            return "group", chat_id[6:]
        elif chat_id.startswith("channel:"):
            return "guild", chat_id[8:]
        elif chat_id.startswith("dm:"):
            return "dm", chat_id[3:]
        else:
            # Default to C2C for plain numeric IDs
            return "c2c", chat_id

    # -------------------------------------------------------------------------
    # WebSocket Connection Management
    # -------------------------------------------------------------------------

    async def _ws_connect(self) -> bool:
        """
        Establish the WebSocket connection to the QQ gateway.

        Protocol (per QQ Open Platform / OpenClaw reference):
          1. WS connect (no auth headers — token goes in WS frames)
          2. Receive op=10 Hello (contains heartbeat_interval)
          3. Send op=2 Identify  (or op=6 Resume if session exists)
          4. Receive op=11 Connected / op=0 READY
          5. Send op=1 Heartbeat at the advertised interval
        """
        if self._is_connecting:
            return False
        self._is_connecting = True

        try:
            account = self._account

            ws_url = self._gateway_url
            logger.info(f"[qq] Connecting to {ws_url} ...")
            self._ws = await websockets.connect(ws_url)

            # Wait for op=10 Hello before sending any identity.
            # QQ server sends Hello immediately on connect.
            hello_payload = await self._ws_recv()
            if hello_payload is None:
                logger.error("[qq] No payload received after WS open (connection closed?)")
                self._is_connecting = False
                return False

            op = hello_payload.get("op")
            logger.info(f"[qq] Received op={op}")

            # Extract heartbeat interval from Hello
            d = hello_payload.get("d", {})
            heartbeat_interval_ms = d.get("heartbeat_interval", 30000)
            logger.info(f"[qq] Hello received, heartbeat_interval={heartbeat_interval_ms}ms")

            # Token format MUST be "QQBot {access_token}" per QQ Bot protocol
            bot_token = f"QQBot {self._access_token}"

            if self._session_id and self._last_seq is not None:
                # Resume existing session
                logger.info(f"[qq] Attempting to resume session {self._session_id}, seq={self._last_seq}")
                resume_payload = {
                    "op": 6,   # Resume
                    "d": {
                        "token": bot_token,
                        "session_id": self._session_id,
                        "seq": self._last_seq,
                    },
                }
                await self._ws.send(json.dumps(resume_payload))
            else:
                # Fresh identify
                logger.info(f"[qq] Sending identify with intents={Intents.FULL} ({Intents.FULL:#x})")
                identify_payload = {
                    "op": 2,   # Identify
                    "d": {
                        "token": bot_token,
                        "intents": Intents.FULL,
                        "shard": [0, 1],
                    },
                }
                await self._ws.send(json.dumps(identify_payload))

            # Start heartbeat task (QQ REQUIRES client-side op=1 heartbeats)
            self._start_heartbeat(heartbeat_interval_ms)

            # Start receive loop
            self._receive_task = asyncio.create_task(self._ws_receive_loop())

            self._reconnect_attempts = 0
            self._last_connect_time = time.time()
            self._is_connecting = False
            return True

        except Exception as e:
            logger.error(f"[qq] WebSocket connect failed: {e}")
            self._is_connecting = False
            await self._schedule_reconnect()
            return False

    async def _ws_recv(self) -> Optional[dict]:
        """Receive and parse a single WebSocket message."""
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=60)
            return json.loads(raw)
        except asyncio.TimeoutError:
            return None
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[qq] WebSocket closed: code={e.code}, reason={e.reason}")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"[qq] Invalid JSON from WS: {e}")
            return None

    async def _ws_receive_loop(self) -> None:
        """Main WebSocket receive loop — processes events until disconnect."""
        while self._is_running and self._ws:
            try:
                payload = await self._ws_recv()
                if payload is None:
                    continue

                op = payload.get("op")
                seq = payload.get("s")
                event_type = payload.get("t")
                data = payload.get("d", {})

                # Save sequence number for session resume
                if seq is not None:
                    self._last_seq = seq
                    self._save_session()

                if op == 0:
                    # Dispatch — incoming event
                    await self._handle_dispatch(event_type, data)
                elif op == 1:
                    # Hello — only expected at connect time, but handle just in case
                    interval = data.get("heartbeat_interval", 30000)
                    self._start_heartbeat(interval)
                elif op == 7:
                    # Heartbeat ACK — server confirmed our heartbeat
                    logger.debug("[qq] Heartbeat ACK received")
                elif op == 11:
                    # Connected event (logged in)
                    logger.info("[qq] WebSocket connected and authenticated")

            except websockets.exceptions.ConnectionClosed as e:
                code = e.code
                reason = e.reason
                logger.warning(f"[qq] WS closed in receive loop: code={code}, reason={reason}")

                # Handle specific close codes (per QQ Bot protocol, OpenClaw gateway.ts)
                if code == 4004:
                    # Invalid token — refresh and reconnect
                    logger.info("[qq] Token invalid (4004), will refresh token")
                    self._should_refresh_token = True
                    break
                elif code == 4008:
                    # Rate limited — will be handled by reconnect delay
                    logger.info("[qq] Rate limited (4008)")
                    break
                elif code in (4006, 4007, 4009):
                    # Session invalid — clear and re-identify
                    logger.info(f"[qq] Session error (code={code}), clearing session")
                    self._session_id = None
                    self._last_seq = None
                    self._should_refresh_token = True
                    break
                elif code == 1000:
                    # Normal close — don't reconnect
                    logger.info("[qq] Normal WS close (1000), not reconnecting")
                    break
                else:
                    # Unexpected close — reconnect
                    break
            except Exception as e:
                logger.error(f"[qq] Error in receive loop: {e}", exc_info=True)
                break

        # Connection lost — schedule reconnect
        if self._is_running:
            await self._schedule_reconnect()

    async def _handle_dispatch(self, event_type: Optional[str], data: dict) -> None:
        """Handle an incoming dispatch event from QQ."""
        if not event_type:
            return

        logger.debug(f"[qq] Dispatch: {event_type}")

        if event_type == "C2C_MESSAGE_CREATE":
            await self._handle_c2c_message(data)
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            await self._handle_group_message(data)
        elif event_type == "DIRECT_MESSAGE_CREATE":
            await self._handle_dm_message(data)
        elif event_type == "CHANNEL_MESSAGE_CREATE":
            await self._handle_channel_message(data)
        elif event_type in ("MESSAGE_AUDIT_CALLBACK", "PUBLIC_MESSAGE_DELETE"):
            # Audit callbacks and delete events — ignore
            pass
        elif event_type == "READY":
            # READY dispatch contains session_id needed for session resume
            self._session_id = data.get("session_id")
            logger.info(f"[qq] READY — session_id={self._session_id}")
        else:
            logger.debug(f"[qq] Unhandled event type: {event_type}")

    # -------------------------------------------------------------------------
    # Message Handlers
    # -------------------------------------------------------------------------

    async def _handle_c2c_message(self, data: dict) -> None:
        """Handle incoming C2C private message."""
        author = data.get("author", {})
        sender_id = str(author.get("id", ""))

        content_raw = data.get("content", "")
        content = extract_text_from_content(content_raw)

        msg_id = str(data.get("id", ""))
        timestamp = data.get("timestamp", "")

        # Handle attachments
        attachments = self._parse_attachments(data.get("attachments", []))

        event = MessageEvent(
            text=content,
            message_type=MessageType.TEXT,
            source=self._make_source("c2c", sender_id, sender_id),
            message_id=msg_id,
            raw_message=data,
        )

        await self.handle_message(event)

    async def _handle_group_message(self, data: dict) -> None:
        """Handle incoming group @-message."""
        author = data.get("author", {})
        sender_id = str(author.get("id", ""))
        group_openid = str(data.get("group_openid", ""))
        group_id = str(data.get("group_id", group_openid))

        content_raw = data.get("content", "")
        content = extract_text_from_content(content_raw)

        msg_id = str(data.get("id", ""))
        timestamp = data.get("timestamp", "")

        attachments = self._parse_attachments(data.get("attachments", []))

        event = MessageEvent(
            text=content,
            message_type=MessageType.TEXT,
            source=self._make_source("group", group_id, sender_id),
            message_id=msg_id,
            raw_message=data,
        )

        await self.handle_message(event)

    async def _handle_dm_message(self, data: dict) -> None:
        """Handle guild DM message."""
        author = data.get("author", {})
        sender_id = str(author.get("id", ""))
        guild_id = str(data.get("guild_id", ""))
        channel_id = str(data.get("channel_id", ""))

        content_raw = data.get("content", "")
        content = extract_text_from_content(content_raw)

        msg_id = str(data.get("id", ""))
        timestamp = data.get("timestamp", "")

        event = MessageEvent(
            text=content,
            message_type=MessageType.TEXT,
            source=self._make_source("dm", guild_id, sender_id),
            message_id=msg_id,
            raw_message=data,
        )

        await self.handle_message(event)

    async def _handle_channel_message(self, data: dict) -> None:
        """Handle guild channel message."""
        author = data.get("author", {})
        sender_id = str(author.get("id", ""))
        guild_id = str(data.get("guild_id", ""))
        channel_id = str(data.get("channel_id", ""))

        content_raw = data.get("content", "")
        content = extract_text_from_content(content_raw)

        msg_id = str(data.get("id", ""))
        timestamp = data.get("timestamp", "")

        event = MessageEvent(
            text=content,
            message_type=MessageType.TEXT,
            source=self._make_source("guild", channel_id, sender_id),
            message_id=msg_id,
            raw_message=data,
        )

        await self.handle_message(event)

    def _parse_attachments(self, raw_attachments: list) -> list:
        """Parse raw attachment list into MessageAttachment objects."""
        from gateway.platforms.qq.types import MessageAttachment

        result = []
        for raw in raw_attachments:
            if not isinstance(raw, dict):
                continue
            result.append(MessageAttachment(
                content_type=raw.get("content_type", ""),
                url=raw.get("url", ""),
                filename=raw.get("filename"),
                height=raw.get("height"),
                width=raw.get("width"),
                size=raw.get("size"),
                voice_wav_url=raw.get("voice_wav_url"),
                asr_refer_text=raw.get("asr_refer_text"),
            ))
        return result

    def _make_source(
        self,
        msg_type: str,
        chat_id: str,
        user_id: str,
    ):
        """Build a SessionSource for MessageEvent.

        SessionSource only has: platform, chat_id, chat_name, chat_type,
        user_id, user_name, thread_id, chat_topic, user_id_alt, chat_id_alt.
        guild_id/channel_id/group_openid are NOT supported — extra context
        is stored in raw_message on the MessageEvent instead.
        """
        from gateway.platforms.base import SessionSource
        return SessionSource(
            platform=Platform.QQ,
            chat_id=chat_id,
            user_id=user_id,
            chat_type=msg_type,
        )

    # -------------------------------------------------------------------------
    # Outbound — enqueue via message queue
    # -------------------------------------------------------------------------

    async def _do_send_message(self, msg: QueuedMessage) -> None:
        """Actual send callback used by the message queue worker."""
        from gateway.platforms.qq import outbound_deliver as deliver

        try:
            token = await qq_api.get_access_token(
                self._account.app_id, self._account.client_secret
            )
            await deliver.send_text_reply(
                access_token=token,
                peer_id=msg.peer_id,
                peer_type=msg.peer_type,
                content=msg.content,
                use_markdown=self._account.markdown_support,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                # Token expired — clear cache and retry once
                qq_api.clear_token_cache(self._account.app_id)
                token = await qq_api.get_access_token(
                    self._account.app_id, self._account.client_secret
                )
                await deliver.send_text_reply(
                    access_token=token,
                    peer_id=msg.peer_id,
                    peer_type=msg.peer_type,
                    content=msg.content,
                )
            else:
                raise

    # -------------------------------------------------------------------------
    # Heartbeat
    # -------------------------------------------------------------------------

    def _start_heartbeat(self, interval_ms: int) -> None:
        """Start the heartbeat task.

        QQ's WebSocket protocol REQUIRES sending op=1 Heartbeat frames at the
        advertised interval. This is what OpenClaw does (gateway.ts line 1265).
        Not sending heartbeats will cause the server to close the connection.
        """
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        async def heartbeat_loop():
            while True:
                await asyncio.sleep(interval_ms / 1000)
                if not self._ws:
                    break
                try:
                    # op=1 Heartbeat: send current seq number
                    hb = {"op": 1, "d": self._last_seq}
                    await self._ws.send(json.dumps(hb))
                    logger.debug(f"[qq] Heartbeat sent (seq={self._last_seq})")
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    logger.warning(f"[qq] Heartbeat send error: {e}")
                    break

        self._heartbeat_task = asyncio.create_task(heartbeat_loop())
        logger.debug(f"[qq] Heartbeat started (interval={interval_ms}ms)")

    # -------------------------------------------------------------------------
    # Reconnect
    # -------------------------------------------------------------------------

    async def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if not self._is_running:
            return
        if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            logger.error(f"[qq] Max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) reached")
            return

        # Quick disconnect detection: if connection lasted < 5s, increment counter
        if self._last_connect_time > 0:
            elapsed = (time.time() - self._last_connect_time) * 1000
            if elapsed < QUICK_DISCONNECT_THRESHOLD_MS:
                self._quick_disconnect_count += 1
                logger.info(
                    f"[qq] Quick disconnect ({elapsed:.0f}ms), "
                    f"count={self._quick_disconnect_count}/{MAX_QUICK_DISCONNECTS}"
                )
                if self._quick_disconnect_count >= MAX_QUICK_DISCONNECTS:
                    logger.error(
                        "[qq] Too many quick disconnects — possible permission or config issue. "
                        "Check: AppID/Secret correct, bot published on QQ Open Platform"
                    )
                    self._quick_disconnect_count = 0
                    delay_ms = 60_000  # Rate limit delay after max quick disconnects
                    self._reconnect_attempts += 1
                    logger.info(f"[qq] Using rate-limit delay {delay_ms}ms (attempt {self._reconnect_attempts})")
                    await asyncio.sleep(delay_ms / 1000)
                    if self._is_running:
                        await self._do_reconnect()
                    return

        delay_ms = RECONNECT_DELAYS_MS[
            min(self._reconnect_attempts, len(RECONNECT_DELAYS_MS) - 1)
        ]
        self._reconnect_attempts += 1

        logger.info(
            f"[qq] Reconnecting in {delay_ms}ms "
            f"(attempt {self._reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})"
        )

        await asyncio.sleep(delay_ms / 1000)

        if self._is_running:
            await self._do_reconnect()

    async def _do_reconnect(self) -> None:
        """Execute a single reconnect attempt."""
        try:
            # Refresh token if needed
            if self._should_refresh_token:
                qq_api.clear_token_cache(self._account.app_id)
                self._should_refresh_token = False

            self._access_token = await qq_api.get_access_token(
                self._account.app_id, self._account.client_secret
            )
            self._gateway_url = await qq_api.get_gateway_url(self._access_token)

            await self._ws_connect()
        except Exception as e:
            logger.error(f"[qq] Reconnect failed: {e}")
            await self._schedule_reconnect()

    # -------------------------------------------------------------------------
    # Session Persistence
    # -------------------------------------------------------------------------

    def _save_session(self) -> None:
        """Save current session state (throttled by session_store)."""
        if not self._account:
            return

        state = SessionState(
            session_id=self._session_id,
            last_seq=self._last_seq,
            last_connected_at=int(time.time() * 1000),
            intent_level_index=0,
            account_id=self._account.account_id,
            saved_at=int(time.time() * 1000),
            app_id=self._account.app_id,
        )
        save_session(state)

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def _cleanup(self) -> None:
        """Release all resources."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._msg_queue:
            await self._msg_queue.stop(timeout=3.0)
            self._msg_queue = None

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None

        self._is_running = False


# =============================================================================
# Requirements check (used by run.py)
# =============================================================================

def check_qq_requirements() -> bool:
    """Return True if required packages are available."""
    try:
        import websockets
        import httpx
        return True
    except ImportError:
        return False
