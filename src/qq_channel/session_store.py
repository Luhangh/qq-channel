"""
QQ Bot WebSocket session persistence.

Persists session_id and last_seq so that on reconnect, we can resume
the WebSocket session without losing message continuity.

Based on OpenClaw's qqbot/src/session-store.ts.
"""

import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

from gateway.platforms.qq.utils import get_qqbot_data_dir, encode_account_id_for_filename

logger = logging.getLogger(__name__)

SESSION_EXPIRE_MS = 5 * 60 * 1000  # 5 minutes
SAVE_THROTTLE_MS = 1000  # 1 second


@dataclass
class SessionState:
    """Persisted WebSocket session state."""
    session_id: Optional[str]
    last_seq: Optional[int]
    last_connected_at: int
    intent_level_index: int
    account_id: str
    saved_at: int
    app_id: Optional[str] = None


def _get_session_path(account_id: str) -> Path:
    """Return the session file path for an account."""
    encoded = encode_account_id_for_filename(account_id)
    return get_qqbot_data_dir("sessions") / f"session-{encoded}.json"


def load_session(account_id: str, app_id: Optional[str] = None) -> Optional[SessionState]:
    """
    Load a saved session for the given account.

    Returns None if:
    - File doesn't exist
    - Session is expired (>5 minutes old)
    - app_id doesn't match (if provided)
    """
    path = _get_session_path(account_id)
    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        state = SessionState(**raw)

        # Check expiry
        if time.time() * 1000 - state.saved_at > SESSION_EXPIRE_MS:
            logger.info(f"[qq:session] Session expired, discarding: {path.name}")
            return None

        # Check app_id matches
        if app_id and state.app_id and state.app_id != app_id:
            logger.info(f"[qq:session] app_id mismatch ({state.app_id} != {app_id}), discarding session")
            return None

        logger.info(f"[qq:session] Restored session: sessionId={state.session_id}, lastSeq={state.last_seq}")
        return state

    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning(f"[qq:session] Failed to load session: {e}")
        return None


# Throttle map: account_id -> (pending_state, last_save_time, throttle_timer)
_throttle_map: dict[str, tuple[SessionState, int, Optional[object]]] = {}


def save_session(state: SessionState) -> None:
    """
    Save session state to disk (throttled to once per SAVE_THROTTLE_MS).
    """
    account_id = state.account_id
    now = time.time() * 1000

    entry = _throttle_map.get(account_id)
    if entry is not None:
        pending_state, last_save_time, _ = entry
        # Update pending state, don't save yet
        _throttle_map[account_id] = (state, last_save_time, None)
        return

    # No pending save — save immediately and set throttle
    _do_save(state)
    _throttle_map[account_id] = (state, now, None)


def _do_save(state: SessionState) -> None:
    """Write session state to disk."""
    path = _get_session_path(state.account_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f)
        logger.debug(f"[qq:session] Saved session: {path.name}")
    except OSError as e:
        logger.warning(f"[qq:session] Failed to save session: {e}")


def clear_session(account_id: str) -> None:
    """Delete the saved session file."""
    _throttle_map.pop(account_id, None)
    path = _get_session_path(account_id)
    if path.exists():
        try:
            path.unlink()
            logger.info(f"[qq:session] Cleared session: {path.name}")
        except OSError:
            pass
