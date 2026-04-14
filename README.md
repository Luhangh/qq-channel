# Hermes QQ Channel

Official QQ Open Platform Bot API adapter for Hermes Agent — enables receiving and sending messages via QQ.

**Key Features:**
- WebSocket long-connection for inbound messages (official QQ protocol)
- HTTP API for outbound messages
- Automatic reconnect + Session Resume
- Hourly token auto-refresh
- Per-peer message rate-limiting queue
- Supports C2C private messages, Group chats, Guild channels

## Installation

```bash
pip install hermes-qq-channel
```

Or install from source:

```bash
git clone https://github.com/your-repo/hermes-qq-channel.git
cd hermes-qq-channel
pip install -e .
```

## Quick Start

### 1. Create a QQ Bot

1. Go to [QQ Open Platform](https://q.qq.com/) and register as a developer
2. Create an application → Add a QQ Bot
3. Obtain your **AppID** and **AppSecret**

### 2. Configure Hermes Agent

Add to `~/.hermes/config.yaml`:

```yaml
platforms:
  qq:
    enabled: true
    extra:
      app_id: "YOUR_APP_ID"          # e.g., 1234567890
      client_secret: "YOUR_APP_SECRET"
      markdown_support: true         # Whether to send Markdown (default true)
      # name: "My Bot"              # Optional: bot display name
      # system_prompt: "..."        # Optional: custom system prompt
```

Or configure via environment variables:

```bash
export QQ_BOT_APP_ID="1234567890"
export QQ_BOT_CLIENT_SECRET="your_client_secret_here"
```

### 3. Restart Hermes Agent

```bash
hermes run
```

Successful connection logs:

```
[qq] Access token obtained for app_id=1234567890
[qq] Connecting to wss://api.sgroup.qq.com/websocket ...
[qq] WebSocket connected and authenticated
```

## Configuration Options

| Option | Env Variable | Type | Default | Description |
|--------|-------------|------|---------|-------------|
| `app_id` | `QQ_BOT_APP_ID` | string | **required** | QQ Open Platform AppID |
| `client_secret` | `QQ_BOT_CLIENT_SECRET` | string | **required** | QQ Open Platform AppSecret |
| `markdown_support` | — | bool | `true` | Whether to send Markdown formatted messages |
| `name` | — | string | — | Bot display name |
| `system_prompt` | — | string | — | Custom system prompt |
| `allow_from` | — | list | `["*"]` | List of allowed user IDs to receive from |

## Supported Message Types

| Type | chat_id format | Description |
|------|---------------|-------------|
| C2C Private | `openid` (plain numeric string) | Private message to user |
| Group Chat | `group:{group_openid}` | Group message (only @-mentions) |
| Guild Channel | `channel:{channel_id}` | Guild channel message |
| Guild DM | `dm:{guild_id}` | Bot-to-user DM channel |

## Project Structure

```
hermes-qq-channel/
├── src/qq_channel/          # Core adapter code
│   ├── api.py               # QQ HTTP API (Token, Gateway, sending)
│   ├── config.py             # Configuration parsing
│   ├── gateway.py            # WebSocket connection & message handling
│   ├── inbound_attachments.py # Inbound attachment processing
│   ├── message_queue.py      # Outbound rate-limiting queue
│   ├── outbound.py           # Outbound message abstraction
│   ├── outbound_deliver.py   # Actual HTTP delivery
│   ├── reply_dispatcher.py   # Reply routing
│   ├── session_store.py      # Session persistence (reconnect/resume)
│   ├── types.py              # Type definitions (WS Payload, Intent, etc.)
│   └── utils.py              # Utilities (CQ code parsing, etc.)
├── tests/                   # Test suite
├── README.md                # English README
├── README_zh.md             # Chinese README
└── pyproject.toml           # Project configuration
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Type checking
mypy src/
```

## Acknowledgements

This adapter is based on:

- [OpenClaw QQBot Extension](https://github.com/openclaw/openclaw/tree/main/extensions/qqbot) — original TypeScript implementation
- [Hermes Agent](https://github.com/nousresearch/hermes-agent) — AI Agent framework

## License

MIT License
