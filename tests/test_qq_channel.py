"""
Tests for Hermes QQ Channel adapter.
"""

import asyncio
import pytest

from qq_channel.config import resolve_qq_account_config, QQBotAccountConfig
from qq_channel.types import Intents, WSPayload, MessageAttachment
from qq_channel.utils import extract_text_from_content, escape_cq_code
from qq_channel.message_queue import MessageQueue, QueuedMessage


class TestConfig:
    def test_resolve_qq_account_config_from_env(self, monkeypatch):
        """Config resolves from environment variables when extra is empty."""
        monkeypatch.setenv("QQ_BOT_APP_ID", "1234567890")
        monkeypatch.setenv("QQ_BOT_CLIENT_SECRET", "test_secret")

        config = resolve_qq_account_config({})
        assert config is not None
        assert config.app_id == "1234567890"
        assert config.client_secret == "test_secret"
        assert config.secret_source == "env"

    def test_resolve_qq_account_config_missing_app_id(self):
        """Returns None when no app_id is provided."""
        config = resolve_qq_account_config({})
        assert config is None

    def test_resolve_qq_account_config_extra_overrides_env(self, monkeypatch):
        """extra dict values take precedence over environment variables."""
        monkeypatch.setenv("QQ_BOT_APP_ID", "env_id")
        monkeypatch.setenv("QQ_BOT_CLIENT_SECRET", "env_secret")

        config = resolve_qq_account_config({
            "app_id": "extra_id",
            "client_secret": "extra_secret",
        })
        assert config is not None
        assert config.app_id == "extra_id"
        assert config.client_secret == "extra_secret"
        assert config.secret_source == "config"

    def test_markdown_support_normalization(self, monkeypatch):
        """markdown_support is normalized to boolean."""
        monkeypatch.setenv("QQ_BOT_APP_ID", "123")
        monkeypatch.setenv("QQ_BOT_CLIENT_SECRET", "secret")

        for val, expected in [("true", True), ("1", True), ("yes", True),
                               ("false", False), ("0", False), ("no", False)]:
            config = resolve_qq_account_config({"markdown_support": val})
            assert config.markdown_support == expected, f"markdown_support={val!r} → {expected}"


class TestIntents:
    def test_intents_values(self):
        """Intents flags have correct power-of-2 values."""
        assert Intents.GUILDS == 1 << 0
        assert Intents.GUILD_MEMBERS == 1 << 1

    def test_intents_full_is_large_value(self):
        """Intents.FULL is the QQ official intent value for all message types."""
        assert Intents.FULL == 1107300352

    def test_intents_bitwise(self):
        """Individual intent flags combine with | correctly."""
        assert (Intents.GUILDS | Intents.GUILD_MEMBERS) == 3


class TestWSPayload:
    def test_ws_payload_dispatch(self):
        """WSPayload can represent a dispatch event."""
        p = WSPayload(op=0, d={"content": "hello"}, s=42, t="C2C_MESSAGE_CREATE")
        assert p.op == 0
        assert p.d["content"] == "hello"
        assert p.s == 42
        assert p.t == "C2C_MESSAGE_CREATE"

    def test_ws_payload_hello(self):
        """WSPayload can represent a hello (op=10)."""
        p = WSPayload(op=10, d={"heartbeat_interval": 41250})
        assert p.op == 10
        assert p.d["heartbeat_interval"] == 41250


class TestMessageAttachment:
    def test_attachment_full(self):
        a = MessageAttachment(
            content_type="image/png",
            url="https://example.com/image.png",
            filename="image.png",
            height=100,
            width=200,
            size=1024,
        )
        assert a.content_type == "image/png"
        assert a.filename == "image.png"
        assert a.height == 100


class TestUtils:
    def test_extract_text_from_content_plain(self):
        """Plain text content is returned as-is."""
        assert extract_text_from_content("hello world") == "hello world"

    def test_extract_text_from_content_list_cq_code(self):
        """CQ codes in list format are stripped during text extraction."""
        # When content is a list, text segments are extracted, CQ codes ignored
        content = [{"type": "text", "data": {"text": "hello world"}},
                   {"type": "image", "data": {"file": "pic.jpg"}}]
        result = extract_text_from_content(content)
        assert result == "hello world"
        assert "CQ:" not in result

    def test_extract_text_from_content_plain_string(self):
        """Plain string content is returned as-is."""
        result = extract_text_from_content("hello [CQ:image,file=pic.jpg] world")
        # String is returned verbatim — CQ code is inside the string itself
        assert result == "hello [CQ:image,file=pic.jpg] world"

    def test_escape_cq_code(self):
        """escape_cq_code prevents CQ injection."""
        escaped = escape_cq_code("test [CQ:image,x=1] data")
        assert "[CQ:" not in escaped
        assert "test" in escaped


class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self):
        """Messages are received in FIFO order."""
        results = []

        async def fake_send(msg: QueuedMessage):
            results.append(msg.content)

        queue = MessageQueue(on_send=fake_send)
        await queue.start(concurrency=1)

        queue.enqueue(QueuedMessage(content="first", peer_id="u1", peer_type="c2c"))
        queue.enqueue(QueuedMessage(content="second", peer_id="u1", peer_type="c2c"))

        # Give workers time to process
        await asyncio.sleep(0.3)
        await queue.stop(timeout=2.0)

        assert results == ["first", "second"]

    @pytest.mark.asyncio
    async def test_urgent_bypass(self):
        """Urgent messages bypass rate limiting."""
        results = []

        async def fake_send(msg: QueuedMessage):
            results.append(msg.content)

        queue = MessageQueue(on_send=fake_send)
        await queue.start(concurrency=1)

        queue.enqueue(QueuedMessage(content="normal", peer_id="u1", peer_type="c2c", is_urgent=False))
        queue.enqueue_urgent("urgent!", "u1", "c2c")

        await asyncio.sleep(0.3)
        await queue.stop(timeout=2.0)

        assert "urgent!" in results
