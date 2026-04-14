"""
Test configuration — add src/ to sys.path so qq_channel imports resolve.

The qq_channel package is normally installed as part of Hermes Agent at:
  gateway/platforms/qq/

For standalone testing we add src/ to the path and provide minimal
shims for the gateway.base module.
"""

import sys
from pathlib import Path

# Add src to path so 'qq_channel' package is importable
_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# Provide minimal shim for gateway.base — only the symbols actually used
# by qq_channel modules.
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum


class Platform(Enum):
    QQ = "qq"


class MessageType(Enum):
    TEXT = "text"


@dataclass
class SessionSource:
    platform: Platform
    chat_id: str
    user_id: str
    chat_type: str


@dataclass
class MessageEvent:
    text: str
    message_type: MessageType
    source: SessionSource
    message_id: str
    raw_message: dict = field(default_factory=dict)


@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class BasePlatformAdapter:
    """Minimal shim — provides the interface used by QQAdapter."""

    def __init__(self, config: Any, platform: Platform):
        self._platform = platform

    async def handle_message(self, event: MessageEvent) -> None:
        """Called by adapter when a message needs processing."""
        pass

    def _mark_connected(self) -> None:
        pass

    def _mark_disconnected(self) -> None:
        pass


# Register shims in a 'gateway' top-level package
import types

gateway = types.ModuleType("gateway")
gateway.Platform = Platform
gateway.platforms = types.ModuleType("gateway.platforms")
gateway.base = types.ModuleType("gateway.platforms.base")

# Attach our classes to gateway.base
setattr(gateway.base, "BasePlatformAdapter", BasePlatformAdapter)
setattr(gateway.base, "MessageEvent", MessageEvent)
setattr(gateway.base, "MessageType", MessageType)
setattr(gateway.base, "SendResult", SendResult)
setattr(gateway.base, "SessionSource", SessionSource)

# Make it a package
gateway.base.__path__ = []  # type: ignore
gateway.platforms.base = gateway.base
gateway.platforms.__path__ = []  # type: ignore
gateway.__path__ = []  # type: ignore

sys.modules["gateway"] = gateway
sys.modules["gateway.platforms"] = gateway.platforms
sys.modules["gateway.platforms.base"] = gateway.base
