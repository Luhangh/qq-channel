"""
QQ Bot outbound message queue with per-peer rate limiting.

Implements token-bucket style rate limiting per user/group.
Urgent commands (like /stop) bypass the queue and execute immediately.

Based on OpenClaw's qqbot/src/message-queue.ts design.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from collections import defaultdict

logger = logging.getLogger(__name__)

# Rate limits (messages per second)
DEFAULT_RATE_LIMIT = 5        # General message send rate
EXCEPTION_RATE_LIMIT = 20     # Error recovery burst
STOP_COMMAND_LIMIT = 100      # /stop etc. can burst through


@dataclass
class QueuedMessage:
    """A queued outbound message awaiting delivery."""
    content: str
    peer_id: str          # openid or group_openid
    peer_type: str        # "c2c" | "group" | "guild"
    retry_count: int = 0
    max_retries: int = 3
    enqueued_at: float = field(default_factory=time.time)
    is_urgent: bool = False   # Bypass queue if True


@dataclass
class PeerRateLimit:
    """Per-peer token bucket state."""
    tokens: float = float(DEFAULT_RATE_LIMIT)
    last_refill: float = field(default_factory=time.time)
    pending_count: int = 0


class MessageQueue:
    """
    FIFO message queue with per-peer token-bucket rate limiting.

    Urgent messages (is_urgent=True) bypass the queue entirely and are
    sent immediately, regardless of rate limits.
    """

    def __init__(
        self,
        rate_limit: float = DEFAULT_RATE_LIMIT,
        on_send: Optional[Callable[[QueuedMessage], Awaitable[None]]] = None,
    ):
        """
        Args:
            rate_limit: Max messages per second per peer.
            on_send: Async callback that actually sends one message.
                     Must raise RateLimitError to trigger refill.
        """
        self._rate_limit = rate_limit
        self._on_send = on_send
        self._queue: asyncio.Queue[Optional[QueuedMessage]] = asyncio.Queue()
        self._peer_limits: dict[str, PeerRateLimit] = defaultdict(PeerRateLimit)
        self._running = False
        self._workers: list[asyncio.Task] = []
        self._stopping = False

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def enqueue(self, msg: QueuedMessage) -> None:
        """Add a message to the queue."""
        self._queue.put_nowait(msg)
        logger.debug(f"[qq:queue] Enqueued msg to {msg.peer_id} (urgent={msg.is_urgent}, queue_size={self._queue.qsize()})")

    async def enqueue_and_wait(self, msg: QueuedMessage) -> None:
        """Enqueue and wait for delivery (for testing)."""
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        # Store future on msg so the worker can resolve it
        msg._future = future  # type: ignore[attr-defined]
        self.enqueue(msg)
        await future

    def enqueue_urgent(self, content: str, peer_id: str, peer_type: str) -> None:
        """Enqueue an urgent message (bypasses rate limit)."""
        self.enqueue(QueuedMessage(
            content=content,
            peer_id=peer_id,
            peer_type=peer_type,
            is_urgent=True,
        ))

    async def start(self, concurrency: int = 3) -> None:
        """Start the queue workers."""
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(concurrency)
        ]
        logger.info(f"[qq:queue] Started {concurrency} workers")

    async def stop(self, timeout: float = 5.0) -> None:
        """Gracefully drain and stop all workers."""
        if not self._running:
            return
        self._stopping = True

        # Signal workers to stop by sending sentinel values
        for _ in self._workers:
            await self._queue.put(None)

        # Wait for workers to finish
        done, pending = await asyncio.wait(
            self._workers, timeout=timeout
        )
        for t in pending:
            t.cancel()
        self._workers.clear()
        self._running = False
        logger.info("[qq:queue] Stopped")

    # -------------------------------------------------------------------------
    # Worker loop
    # -------------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        """Single queue worker that processes messages in order."""
        while not self._stopping:
            msg = await self._queue.get()

            if msg is None:
                # Sentinel — graceful shutdown
                break

            try:
                if msg.is_urgent:
                    await self._send_immediate(msg)
                else:
                    await self._send_with_limit(msg)
            except RateLimitError:
                # Re-enqueue with delay
                await asyncio.sleep(0.5)
                self.enqueue(msg)
            except Exception as e:
                logger.error(f"[qq:queue] Send error for {msg.peer_id}: {e}")
                if msg.retry_count < msg.max_retries:
                    msg.retry_count += 1
                    await asyncio.sleep(1.0 * msg.retry_count)
                    self.enqueue(msg)
            finally:
                self._queue.task_done()

    async def _send_with_limit(self, msg: QueuedMessage) -> None:
        """Send a message after acquiring a rate-limit token."""
        limit = self._peer_limits[msg.peer_id]

        # Refill tokens based on elapsed time
        now = time.time()
        elapsed = now - limit.last_refill
        limit.tokens = min(self._rate_limit, limit.tokens + elapsed * self._rate_limit)
        limit.last_refill = now

        if limit.tokens < 1.0:
            # Wait until we have at least 1 token
            wait_time = (1.0 - limit.tokens) / self._rate_limit
            logger.debug(f"[qq:queue] Rate limited {msg.peer_id}, waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
            limit.tokens = 0.0
            limit.last_refill = time.time()
        else:
            limit.tokens -= 1.0

        if self._on_send:
            await self._on_send(msg)
        else:
            logger.warning(f"[qq:queue] No send handler configured for {msg.peer_id}")

    async def _send_immediate(self, msg: QueuedMessage) -> None:
        """Immediately send without rate limiting (for urgent commands)."""
        logger.debug(f"[qq:queue] Immediate send to {msg.peer_id}")
        if self._on_send:
            await self._on_send(msg)
        else:
            logger.warning(f"[qq:queue] No send handler for urgent msg to {msg.peer_id}")


class RateLimitError(Exception):
    """Raised when a peer is rate-limited."""
    pass
