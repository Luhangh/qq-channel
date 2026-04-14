"""
QQ Bot configuration parsing.

Reads app_id and client_secret from environment variables or config.yaml.
Mirrors the resolveQQBotAccount() logic from OpenClaw's qqbot/src/config.ts.
"""

import logging
import os
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_ACCOUNT_ID = "default"


@dataclass
class QQBotAccountConfig:
    """Resolved QQ Bot account config for runtime."""
    account_id: str
    app_id: str
    client_secret: str
    secret_source: str  # "env" | "config" | "file"
    enabled: bool = True
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    markdown_support: bool = True
    allow_from: list[str] = None

    def __post_init__(self):
        if self.allow_from is None:
            self.allow_from = ["*"]


def resolve_qq_account_config(config_extra: dict) -> Optional[QQBotAccountConfig]:
    """
    Resolve QQ Bot account config from platform config.extra dict.

    Config precedence:
    1. app_id from config.extra (env QQ_BOT_APP_ID also checked)
    2. client_secret from config.extra or env QQ_BOT_CLIENT_SECRET

    Returns None if no app_id is configured.
    """
    # Read app_id
    app_id = (config_extra.get("app_id") or "").strip()
    if not app_id:
        app_id = os.environ.get("QQ_BOT_APP_ID", "").strip()
    if not app_id:
        return None

    # Read client_secret
    client_secret = (config_extra.get("client_secret") or "").strip()
    secret_source = "config"

    if not client_secret:
        client_secret = os.environ.get("QQ_BOT_CLIENT_SECRET", "").strip()
        secret_source = "env"

    if not client_secret:
        logger.warning("[qq] No QQ_BOT_CLIENT_SECRET configured — bot will not be able to send messages")
        client_secret = ""
        secret_source = "none"

    # Read optional config
    name = config_extra.get("name")
    system_prompt = config_extra.get("system_prompt")
    markdown_support = config_extra.get("markdown_support", True)
    allow_from = config_extra.get("allow_from", ["*"])

    # Normalize markdown_support
    if isinstance(markdown_support, str):
        markdown_support = markdown_support.lower() in ("true", "1", "yes", "on")

    return QQBotAccountConfig(
        account_id=DEFAULT_ACCOUNT_ID,
        app_id=app_id,
        client_secret=client_secret,
        secret_source=secret_source,
        enabled=True,
        name=name,
        system_prompt=system_prompt,
        markdown_support=markdown_support,
        allow_from=allow_from,
    )
