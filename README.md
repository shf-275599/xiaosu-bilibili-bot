# Bilibili 评论自动回复机器人 v2

自动监听 Bilibili 评论和私信，使用 DeepSeek V4 Flash 生成 AI 回复的后台守护进程。

## 功能特性

- **Pipeline 管道架构** - 模块化处理流程（dedup → filter → rate_limit → generate → safety → send）
- **多来源监听** - 消息通知回复、@我消息、自己动态评论、自己视频评论、私信
- **AI 智能回复** - DeepSeek V4 Flash，支持 Function Calling（Tool Calling）
- **上下文富化** - 自动携带视频标题、父评论内容、私信对话历史、BV号
- **视频内容工具** - 用户 @bot 可获取视频 AI 摘要，不可用时自动降级为 Whisper 语音转录
- **联网搜索** - DuckDuckGo 零配置搜索，用户问"今天有什么新闻"时自动查
- **Cookie 自动刷新** - RSA-OAEP 加密 + refresh_csrf 完整链路
- **DM 私信回复** - 监听私信，AI 生成回复并发送（含 WBI 签名）
- **保守风控** - 随机延迟、来源熔断、全局熔断、小时/日回复上限
- **类型安全配置** - Pydantic v2 配置验证
- **结构化日志** - structlog JSON 格式，便于监控

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
pip install ddgs  # 联网搜索依赖
```

### 2. 配置

```bash
# 编辑配置文件
nano .env                              # 填入 DEEPSEEK_API_KEY
nano config/bilibili-cookies.txt       # 填入真实 Cookies
nano config/bot-config.toml            # 调整机器人配置
```

### 3. 测试运行

```bash
# dry-run（生成回复但不发送）
python -m bilibili_bot --once --dry-run

# 单轮真实运行
python -m bilibili_bot --once
```

### 4. 启动守护模式

```bash
# systemd（推荐，开机自启）
systemctl --user enable --now bilibot

# 手动启动
systemctl --user start bilibot

# 查看日志
journalctl --user -u bilibot -f
```

### 5. 开机自启

```bash
# 允许用户服务在系统启动时自动运行（需要一次 sudo）
sudo loginctl enable-linger $USER
```

## 配置说明

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | 是 |
| `BILIBILI_REFRESH_TOKEN` | Cookie 自动刷新 Token | 否 |

### 主要配置

```toml
[bot]
poll_interval_seconds = 5     # 主循环间隔

[sources.msgfeed]
enabled = true                # 消息通知回复
poll_interval_seconds = 8

[sources.mention]
enabled = true                # @我 消息

[sources.dm]
enabled = true                # 私信自动回复
poll_interval_seconds = 5

[ai]
primary_provider = "deepseek" # DeepSeek V4 Flash
tools_enabled = true           # 启用 Function Calling
tool_max_iterations = 3       # 工具调用最大轮次

[ai.providers.deepseek]
type = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"

[rate_limit]
min_request_interval_seconds = 0.5
reply_delay_min_seconds = 0.3
reply_delay_max_seconds = 1.2
max_hourly_replies = 20
max_daily_replies = 100
```

## 用户使用方式

**视频总结**：在任何 B站视频评论区 @bot 账号，发送 "总结一下" 或 "这视频讲了什么"。

Bot 自动调用 `get_video_content` 工具：
1. 先尝试 B站 AI 摘要（2-10s）
2. 不可用时自动降级为 Whisper 语音转录（30-90s）
3. 转录结果缓存，同一视频不重复转录

**联网搜索**：在私信或评论中问 "今天有什么新闻"、"查一下XX"，Bot 自动调用 `search_web` 工具，通过 DuckDuckGo 搜索并返回结果摘要。

## 目录结构

```
bilibili-bot/
├── src/bilibili_bot/          # 源代码
│   ├── __main__.py           # 入口
│   ├── config.py             # Pydantic 配置
│   ├── client.py             # HTTP 客户端（WBI 签名）
│   ├── events.py             # 事件模型
│   ├── state.py              # 状态存储（带文件锁）
│   ├── cookie.py             # Cookie 刷新管理
│   ├── pipeline/             # 处理管道
│   │   ├── base.py           # PipelineStage ABC
│   │   ├── dedup.py          # 去重
│   │   ├── filter.py         # 过滤
│   │   ├── rate_limit.py     # 频控 + 熔断
│   │   ├── generate.py       # AI 生成（含 Tool Calling）
│   │   ├── safety.py         # 内容安全审查
│   │   └── send.py           # 发送（含 DM WBI 签名）
│   ├── providers/            # AI Provider
│   │   ├── base.py           # Provider ABC
│   │   ├── openai_compat.py  # OpenAI 兼容（含 tool calling）
│   │   └── manager.py        # Provider 管理
│   ├── tools/                # LLM Function Calling 工具
│   │   ├── __init__.py       # 工具定义 + 执行（摘要/转录/搜索）
│   │   ├── transcribe.py     # Whisper 语音转录
│   │   └── web_search.py     # 联网搜索
│   └── sources/              # 数据来源
│       ├── base.py           # Source ABC
│       ├── msgfeed.py        # 消息通知 + bvid 补充
│       ├── mention.py        # @我消息 + bvid 补充
│       ├── own_video.py      # 自己视频评论
│       ├── own_dynamic.py    # 自己动态评论
│       └── dm.py             # 私信
├── scripts/                  # 辅助脚本
│   └── bilibili_wbi.py       # WBI 签名 + AI 摘要
├── models/whisper/           # Whisper 模型（gitignored）
├── config/                   # 配置文件
├── data/                     # 运行时数据（gitignored）
├── tests/                    # 单元测试
├── bilibot.service           # systemd 服务文件
├── pyproject.toml
└── README.md
```

## 架构设计

### Pipeline 管道

```
Source.fetch() → [Event]
    ↓
DedupStage      → 跳过已处理（冷却 5 分钟后重试，致命错误 1 小时后过期）
FilterStage     → 跳过自己/空/黑名单（DM 跳过此阶段）
RateLimitStage  → 频控检查 + 等待
GenerateStage   → DeepSeek 生成回复（含 Tool Calling）
SafetyStage     → 敏感词/PII/链接/长度四重检查
SendStage       → 发送到 Bilibili API（评论 + 私信 WBI 签名）
```

### Tool Calling 流程

```
用户评论 "总结一下这个视频"
    ↓
GenerateStage → 构建 prompt（含 bvid）
    ↓
DeepSeek API（带 tools 定义）
    ├─ 无需工具 → 直接生成回复
    └─ tool_calls: get_video_content(bvid)
         ├─ AI 摘要可用 → 返回摘要 → 生成回复
         └─ AI 摘要不可用 → Whisper 转录降级 → 生成回复
```

### 事件模型

```python
@dataclass
class CommentEvent(Event):
    business_type: str      # "video" | "dynamic" | "dynamic_draw"
    oid: str                # 内容 ID
    bvid: str               # BV号（自动 aid→bvid 转换）
    rpid: str               # 评论 ID
    author_mid: str         # 评论者 UID
    author_name: str        # 评论者昵称
    content_text: str       # 评论内容
    video_title: str        # 视频/动态标题（自动注入）
    parent_content: str     # 父评论内容（楼中楼上下文）
    at_me: bool             # 是否 @了 bot

@dataclass
class DMEvent(Event):
    talker_id: int          # 对话对方 UID
    talker_name: str        # 对方昵称
    dm_content: str         # 私信内容
    recent_messages: list   # 最近对话历史（5条）
```

## 风控策略

- **随机延迟** - 每条回复前等待 0.3-1.2s
- **来源熔断** - 单源连续失败 3 次 → 冷却 180s
- **全局熔断** - 连续失败 5 次 → 冷却 600s
- **去重冷却** - 失败重试最多 5 次，冷却 5 分钟，致命错误 1 小时后过期
- **小时上限** - 每小时最多 20 条
- **日上限** - 每天最多 100 条
- **转录冷却** - 两次语音转录间隔 30s

## 管理命令

```bash
# systemd（推荐）
systemctl --user status bilibot     # 状态
systemctl --user restart bilibot    # 重启
journalctl --user -u bilibot -f     # 实时日志

# 单轮测试
python -m bilibili_bot --once --dry-run
python -m bilibili_bot --once
```

## 许可证

MIT
