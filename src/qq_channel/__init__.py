"""
Hermes QQ Channel — standalone QQ Bot adapter for Hermes Agent.

When installed into Hermes Agent, this package is accessed as:
    from gateway.platforms.qq.gateway import QQAdapter, check_qq_requirements

When testing standalone, import individual modules directly:
    from qq_channel.config import resolve_qq_account_config
    from qq_channel.types import Intents, WSPayload
    from qq_channel.message_queue import MessageQueue
    etc.
"""

# Public API — only modules that don't require gateway.platforms.base
from qq_channel.config import resolve_qq_account_config, QQBotAccountConfig
from qq_channel.types import Intents, WSPayload, MessageAttachment
from qq_channel.utils import extract_text_from_content, escape_cq_code
from qq_channel.message_queue import MessageQueue, QueuedMessage, RateLimitError

__all__ = [
    "resolve_qq_account_config",
    "QQBotAccountConfig",
    "Intents",
    "WSPayload",
    "MessageAttachment",
    "extract_text_from_content",
    "escape_cq_code",
    "MessageQueue",
    "QueuedMessage",
    "RateLimitError",
]
