"""
QQ Open Platform HTTP API client.

Based on OpenClaw's qqbot/src/api.ts:
- API_BASE = https://api.sgroup.qq.com
- Token URL from QQ开放平台 docs
- Authorization: QQBot {access_token}
"""

import asyncio
import logging
import time
import urllib.parse
import uuid
from typing import Optional, Any

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

API_BASE = "https://api.sgroup.qq.com"
DEFAULT_API_TIMEOUT = 30_000  # ms
FILE_UPLOAD_TIMEOUT = 120_000  # ms

# =============================================================================
# Token Cache (per app_id, singleflight)
# =============================================================================

_token_cache: dict[str, dict] = {}
_token_fetch_promises: dict[str, asyncio.Future] = {}


async def get_access_token(app_id: str, client_secret: str) -> str:
    """
    Fetch and cache an access token for the given app_id.

    Uses singleflight semantics to avoid concurrent token fetches.
    Caches token with expiry; refreshes 5 minutes before expiration.
    """
    cached = _token_cache.get(app_id)

    # Refresh slightly ahead of expiry, but not so early that short-lived tokens become unusable
    refresh_ahead_ms = (cached["expires_at"] - time.time() * 1000) // 3 if cached else 0
    if cached and (time.time() * 1000) < cached["expires_at"] - refresh_ahead_ms:
        return cached["token"]

    # Singleflight: if a fetch is already in progress, wait for it
    if app_id in _token_fetch_promises:
        logger.debug(f"[qq:api] Token fetch in progress for {app_id}, waiting...")
        return await _token_fetch_promises[app_id]

    # Start new fetch
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _token_fetch_promises[app_id] = future

    try:
        token = await _do_fetch_token(app_id, client_secret)
        future.set_result(token)
        return token
    except Exception as e:
        future.set_exception(e)
        raise
    finally:
        _token_fetch_promises.pop(app_id, None)


async def _do_fetch_token(app_id: str, client_secret: str) -> str:
    """Perform the actual token fetch request."""
    import httpx

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": f"QQBotPlugin/1.0 (Python/{__import__('sys').version.split()[0]})",
    }

    body = {"app_id": app_id, "client_secret": client_secret}

    # Token endpoint: https://bots.qq.com/app/getAppAccessToken
    # (discovered via hex dump of OpenClaw binary - this URL is redacted in source)
    token_url = "https://bots.qq.com/app/getAppAccessToken"

    logger.info(f"[qq:api] Fetching access token for app_id={app_id}")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(token_url, data=body, headers=headers)
        resp.raise_for_status()
        raw_body = resp.text

    logger.debug(f"[qq:api] Token response: {raw_body[:100]}")

    # Response is form-encoded: access_token=...&expires_in=5124
    parsed = urllib.parse.parse_qs(raw_body)
    access_token = parsed.get("access_token", [None])[0]
    expires_in_str = parsed.get("expires_in", ["7200"])[0]

    if not access_token:
        raise RuntimeError(f"Failed to get access_token: {raw_body[:200]}")

    expires_in = int(expires_in_str)

    _token_cache[app_id] = {
        "token": access_token,
        "expires_at": time.time() * 1000 + expires_in * 1000,
        "app_id": app_id,
    }

    logger.info(f"[qq:api] Token cached for app_id={app_id}, expires in {expires_in}s")
    return access_token


def clear_token_cache(app_id: Optional[str] = None) -> None:
    """Clear one or all cached tokens."""
    if app_id:
        _token_cache.pop(app_id, None)
        logger.debug(f"[qq:api] Token cache cleared for {app_id}")
    else:
        _token_cache.clear()
        logger.debug("[qq:api] All token caches cleared")


# =============================================================================
# HTTP API Request Helper
# =============================================================================

async def api_request(
    access_token: str,
    method: str,
    path: str,
    body: Optional[dict] = None,
    timeout_ms: Optional[int] = None,
) -> dict:
    """
    Make an authenticated HTTP request to the QQ Open Platform API.

    Args:
        access_token: QQ Bot access token
        method: HTTP method (GET, POST, etc.)
        path: API path (e.g. "/gateway")
        body: Request body (for POST/PUT/PATCH)
        timeout_ms: Request timeout in milliseconds

    Returns:
        Parsed JSON response dict
    """
    import httpx

    url = f"{API_BASE}{path}"
    is_file_upload = "/files" in path
    timeout = timeout_ms or (FILE_UPLOAD_TIMEOUT if is_file_upload else DEFAULT_API_TIMEOUT)

    headers = {
        "Authorization": f"QQBot {access_token}",
        "Content-Type": "application/json",
        "User-Agent": f"QQBotPlugin/1.0 (Python/{__import__('sys').version.split()[0]})",
    }

    logger.debug(f"[qq:api] {method} {url} (timeout={timeout}ms)")

    async with httpx.AsyncClient(timeout=float(timeout) / 1000) as client:
        if body is not None:
            resp = await client.request(method, url, json=body, headers=headers)
        else:
            resp = await client.request(method, url, headers=headers)

        resp.raise_for_status()
        return resp.json()


async def api_request_with_retry(
    access_token: str,
    method: str,
    path: str,
    body: Optional[dict] = None,
    max_retries: int = 2,
) -> dict:
    """
    Make an API request with exponential backoff retry on transient failures.

    Does NOT retry on 400, 401, or timeout errors (those are considered fatal).
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await api_request(access_token, method, path, body)
        except Exception as e:
            last_error = e
            err_str = str(e).lower()

            # Don't retry on auth or timeout errors
            if any(x in err_str for x in ("400", "401", "invalid", "timeout")):
                raise

            if attempt < max_retries:
                delay = 1.0 * (2 ** attempt)
                logger.warning(f"[qq:api] Attempt {attempt+1} failed: {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)

    raise last_error


# =============================================================================
# Gateway URL
# =============================================================================

async def get_gateway_url(access_token: str) -> str:
    """
    Get the QQ Open Platform WebSocket gateway URL.

    GET /gateway
    Returns: {"url": "wss://api.sgroup.qq.com/..."}
    """
    data = await api_request(access_token, "GET", "/gateway")
    url = data.get("url")
    if not url:
        raise RuntimeError(f"Failed to get gateway URL: {data}")
    logger.info(f"[qq:api] Gateway URL obtained: {url[:60]}...")
    return url


# =============================================================================
# Message Sending APIs
# =============================================================================

async def send_c2c_message(
    app_id: str,
    access_token: str,
    openid: str,
    content: str,
    msg_id: Optional[str] = None,
    message_reference: Optional[str] = None,
    use_markdown: bool = False,
) -> dict:
    """
    Send a private (C2C) message.

    POST /v2/users/{openid}/messages
    Body: {"content": "...", "msg_type": 0}  or {"markdown": {"content": "..."}, "msg_type": 2}
    """
    msg_seq = _get_next_msg_seq(msg_id) if msg_id else 1

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

    result = await api_request(access_token, "POST", f"/v2/users/{openid}/messages", body)
    logger.info(f"[qq:api] C2C message sent to {openid}: {content[:50]}...")
    return result


async def send_c2c_input_notify(
    access_token: str,
    openid: str,
    msg_id: Optional[str] = None,
    input_second: int = 60,
) -> dict:
    """
    Send typing indicator to a C2C chat.

    POST /v2/users/{openid}/messages
    Body: {"msg_type": 6, "input_notify": {"input_type": 1, "input_second": 60}, ...}
    """
    msg_seq = _get_next_msg_seq(msg_id) if msg_id else 1

    body = {
        "msg_type": 6,
        "input_notify": {
            "input_type": 1,
            "input_second": input_second,
        },
        "msg_seq": msg_seq,
    }
    if msg_id:
        body["msg_id"] = msg_id

    return await api_request(access_token, "POST", f"/v2/users/{openid}/messages", body)


async def send_group_message(
    app_id: str,
    access_token: str,
    group_openid: str,
    content: str,
    msg_id: Optional[str] = None,
    use_markdown: bool = False,
) -> dict:
    """
    Send a group message.

    POST /v2/groups/{groupOpenid}/messages
    """
    msg_seq = _get_next_msg_seq(msg_id) if msg_id else 1

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

    result = await api_request(access_token, "POST", f"/v2/groups/{group_openid}/messages", body)
    logger.info(f"[qq:api] Group message sent to {group_openid}: {content[:50]}...")
    return result


async def send_channel_message(
    access_token: str,
    channel_id: str,
    content: str,
    msg_id: Optional[str] = None,
) -> dict:
    """
    Send a message to a guild channel.

    POST /channels/{channelId}/messages
    """
    body: dict = {"content": content}
    if msg_id:
        body["msg_id"] = msg_id

    return await api_request(access_token, "POST", f"/channels/{channel_id}/messages", body)


async def send_dm_message(
    access_token: str,
    guild_id: str,
    content: str,
    msg_id: Optional[str] = None,
) -> dict:
    """
    Send a direct message inside a guild DM session.

    POST /dms/{guildId}/messages
    """
    body: dict = {"content": content}
    if msg_id:
        body["msg_id"] = msg_id

    return await api_request(access_token, "POST", f"/dms/{guild_id}/messages", body)


# =============================================================================
# Utility
# =============================================================================

def _get_next_msg_seq(msg_id: str) -> int:
    """Generate a message sequence number in 0..65535 range."""
    time_part = int(time.time() * 1000) % 100_000_000
    random_part = uuid.uuid4().int % 65536
    return (time_part ^ random_part) % 65536
