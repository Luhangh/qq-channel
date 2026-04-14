# Hermes QQ Channel

QQ Open Platform 官方 Bot API 适配器，让 Hermes Agent 通过 QQ 接收和发送消息。

**主要特性：**
- WebSocket 长连接接收消息（QQ 官方协议）
- HTTP API 发送消息
- 自动重连 + Session Resume
- 每小时 Token 自动刷新
- 每-peer 消息限速队列
- 支持 C2C 私信、群聊、Guild 频道

## 安装 / Installation

**通过 pip 安装（来自 GitHub）：**

```bash
pip install git+https://github.com/Luhangh/qq-channel.git
```

**或从源码安装：**

```bash
git clone https://github.com/Luhangh/qq-channel.git
cd hermes-qq-channel
pip install -e .
python install.py --hermes-dir ~/.hermes/hermes-agent
```

## 快速开始

### 1. 申请 QQ Bot

1. 前往 [QQ 开放平台](https://q.qq.com/) 注册开发者账号
2. 创建应用 → 添加 QQ Bot 机器人
3. 获取 **AppID** 和 **AppSecret**

### 2. 配置 Hermes Agent

在 `~/.hermes/config.yaml` 中添加：

```yaml
platforms:
  qq:
    enabled: true
    extra:
      app_id: "你的AppID"          # 例如: 1234567890
      client_secret: "你的AppSecret"
      markdown_support: true      # 是否支持 Markdown（默认 true）
      # name: "My Bot"           # 可选：机器人名称
      # system_prompt: "..."     # 可选：系统提示词
```

或通过环境变量配置：

```bash
export QQ_BOT_APP_ID="1234567890"
export QQ_BOT_CLIENT_SECRET="your_client_secret_here"
```

### 3. 重启 Hermes Agent

```bash
hermes run
```

看到以下日志说明连接成功：

```
[qq] Access token obtained for app_id=1234567890
[qq] Connecting to wss://api.sgroup.qq.com/websocket ...
[qq] WebSocket connected and authenticated
```

## 配置选项

| 选项 | 环境变量 | 类型 | 默认值 | 说明 |
|------|---------|------|--------|------|
| `app_id` | `QQ_BOT_APP_ID` | string | **必填** | QQ 开放平台 AppID |
| `client_secret` | `QQ_BOT_CLIENT_SECRET` | string | **必填** | QQ 开放平台 AppSecret |
| `markdown_support` | — | bool | `true` | 是否发送 Markdown 格式消息 |
| `name` | — | string | — | 机器人显示名称 |
| `system_prompt` | — | string | — | 自定义系统提示词 |
| `allow_from` | — | list | `["*"]` | 允许接收消息的用户 ID 列表 |

## 消息类型支持

| 类型 | chat_id 格式 | 说明 |
|------|-------------|------|
| C2C 私信 | `openid`（纯数字字符串） | 私聊用户 |
| 群聊 | `group:{group_openid}` | 群聊（仅 @ 机器人消息） |
| Guild 频道 | `channel:{channel_id}` | 频道消息 |
| Guild DM | `dm:{guild_id}` | 机器人与用户的私信频道 |

## 目录结构

```
hermes-qq-channel/
├── src/qq_channel/          # 核心适配器代码
│   ├── api.py               # QQ HTTP API 调用（Token、Gateway、发送消息）
│   ├── config.py             # 配置解析
│   ├── gateway.py            # WebSocket 连接管理与消息处理
│   ├── inbound_attachments.py # 接收附件处理
│   ├── message_queue.py      # 发送消息限速队列
│   ├── outbound.py           # 发送消息抽象层
│   ├── outbound_deliver.py   # 实际 HTTP 发送实现
│   ├── reply_dispatcher.py   # 回复路由
│   ├── session_store.py      # Session 持久化（断线重连用）
│   ├── types.py              # 类型定义（WebSocket Payload、Intent 等）
│   └── utils.py              # 工具函数（CQ 码解析等）
├── tests/                    # 测试用例
├── README.md                 # 本文件
├── README_zh.md              # 中文版自述文件
└── pyproject.toml            # 项目配置
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 类型检查
mypy src/
```

## 参考项目

本适配器参考了以下开源项目：

- [OpenClaw QQBot Extension](https://github.com/openclaw/openclaw/tree/main/extensions/qqbot) — 原始 TypeScript 实现
- [Hermes Agent](https://github.com/nousresearch/hermes-agent) — AI Agent 框架

## 协议

MIT License
