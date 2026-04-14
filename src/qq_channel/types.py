"""
QQ Bot message types and WebSocket payload structures.

Matches the QQ Open Platform API types from OpenClaw's qqbot extension.
https://github.com/openclaw/openclaw/tree/main/extensions/qqbot
"""

from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Message Attachments
# =============================================================================

@dataclass
class MessageAttachment:
    """Rich-media attachment metadata."""
    content_type: str
    url: str
    filename: Optional[str] = None
    height: Optional[int] = None
    width: Optional[int] = None
    size: Optional[int] = None
    voice_wav_url: Optional[str] = None
    asr_refer_text: Optional[str] = None  # Speech recognition text for voice


# =============================================================================
# Incoming Message Events
# =============================================================================

@dataclass
class C2CMessageEvent:
    """C2C (private chat) message event payload."""
    author: dict  # {id, union_openid, user_openid}
    content: str
    id: str
    timestamp: str
    message_scene: Optional[dict] = None  # {source, ext?}
    attachments: list[MessageAttachment] = field(default_factory=list)


@dataclass
class GuildMessageEvent:
    """Guild channel message event payload (also used for DMs)."""
    id: str
    channel_id: str
    guild_id: str
    content: str
    timestamp: str
    author: dict  # {id, username?, bot?}
    member: Optional[dict] = None  # {nick?, joined_at?}
    attachments: list[MessageAttachment] = field(default_factory=list)


@dataclass
class GroupMessageEvent:
    """Group @-message event payload."""
    author: dict  # {id, member_openid}
    content: str
    id: str
    timestamp: str
    group_id: str
    group_openid: str
    message_scene: Optional[dict] = None  # {source, ext?}
    attachments: list[MessageAttachment] = field(default_factory=list)


# =============================================================================
# WebSocket Payload
# =============================================================================

@dataclass
class WSPayload:
    """
    QQ Open Platform WebSocket event envelope.

    op:
        0 = dispatch     → incoming event from QQ server
        1 = hello        → sent on connect, contains heartbeat interval
        2 = resume       → resume a previous session
        7 = heartbeat ACK → server acknowledged our heartbeat
    """
    op: int
    d: Optional[dict] = None
    s: Optional[int] = None  # sequence number
    t: Optional[str] = None  # event type


# =============================================================================
# QQ Open Platform Intent Flags
# =============================================================================

class Intents:
    """
    QQ Open Platform WebSocket intent flags.
    Combine with | to request multiple event types.
    """
    GUILDS = 1 << 0
    GUILD_MEMBERS = 1 << 1
    PUBLIC_GUILD_MESSAGES = 1 << 30
    DIRECT_MESSAGE = 1 << 12
    GROUP_AND_C2C = 1 << 25

    # All message types we handle
    FULL = PUBLIC_GUILD_MESSAGES | DIRECT_MESSAGE | GROUP_AND_C2C


# =============================================================================
# Internal normalized message (used by gateway.py → reply_dispatcher)
# =============================================================================

@dataclass
class NormalizedQQMessage:
    """
    Normalized message shape used internally by the QQ adapter.
    Produced by gateway.py after parsing WSPayload, consumed by
    reply_dispatcher.py and message_queue.py.
    """
    type: str          # "c2c" | "group" | "guild" | "dm"
    sender_id: str
    sender_name: Optional[str] = None
    content: str = ""
    message_id: str = ""
    timestamp: str = ""
    channel_id: Optional[str] = None
    guild_id: Optional[str] = None
    group_openid: Optional[str] = None
    attachments: list[MessageAttachment] = field(default_factory=list)
    # For quote replies (ref_idx from outbound messages)
    ref_msg_idx: Optional[str] = None
    msg_idx: Optional[str] = None
