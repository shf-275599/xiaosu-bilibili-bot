# Bilibili 评论自动回复机器人 v2

自动监听 Bilibili 评论和私信，使用 DeepSeek V4 Flash 生成 AI 回复的后台守护进程。

## 功能特性

- **Pipeline 管道架构** - 模块化处理流程（dedup → filter → rate_limit → generate → safety → send）
- **多来源监听** - 消息通知回复、@我消息、自己视频/动态评论、私信；支持视频、图文动态、纯文字动态、专栏文章四种内容类型
- **AI 智能回复** - DeepSeek V4 Flash，基于 PydanticAI Agent 的工具调用（Tool Calling）
- **上下文富化** - 自动携带视频标题/动态内容/文章摘要、父评论内容（楼中楼）、私信对话历史、BV号、用户画像、视频热度
- **楼中楼上下文** - @bot 在他人评论下回复时，自动提取被回复的评论内容作为上下文
- **视频内容工具** - 用户 @bot 可获取视频 AI 摘要，不可用时自动降级为 Whisper 语音转录
- **图文动态图片** - 提取图文动态中的图片 URL，注入图片数量到 prompt（为视觉模型预留接口）
- **联网搜索** - Tavily 优先（每日配额），自动降级到 DuckDuckGo
- **Cookie 自动刷新** - RSA-OAEP 加密 + refresh_csrf 完整链路
- **DM 私信回复** - 监听私信，AI 生成回复并发送（含 WBI 签名）
- **保守风控** - 随机延迟、来源熔断、全局熔断、小时/日回复上限、单用户/单内容频控
- **类型安全配置** - Pydantic v2 配置验证
- **结构化日志** - structlog JSON 格式，便于监控
- **工具调用日志** - PydanticAI Tool 包装器自动记录每次工具调用的参数和结果
- **每日统计报告** - 定时向主人推送当日回复/工具调用/错误统计
- **自动跳过** - 同一用户反复触发 fatal 错误时自动跳过，节省 API 调用
- **多轮对话记忆** - 私信超 30 条时自动总结对话背景；评论连续对话持久化到 `bot-state.json`
- **评论连续对话** - 追踪同一用户在同一视频下的连续对话，保持回复连贯性
- **回复质量反馈** - 每 6 小时检查最近 7 天自己回复的点赞/回复数，追踪回复效果

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -e .
pip install ddgs faster-whisper  # 联网搜索 + 语音转录依赖

# 下载 Whisper 模型（首次运行 faster-whisper 时会自动下载，约 142MB）
# 或手动预下载：faster-whisper-download base --output-dir models/whisper/
```

### 2. 配置

```bash
# 编辑配置文件
cp .env.example .env
nano .env                              # 填入 DEEPSEEK_API_KEY 和 BILIBILI_REFRESH_TOKEN
nano config/bilibili-cookies.txt       # 填入真实 Cookies（Netscape 格式）
nano config/bot-config.toml            # 调整机器人配置（可选，默认值即可运行）
```

### 3. 测试运行

```bash
# dry-run（生成回复但不发送）
.venv/bin/python3 -m bilibili_bot --once --dry-run

# 单轮真实运行
.venv/bin/python3 -m bilibili_bot --once
```

### 4. 安装 systemd 服务

```bash
# 复制服务文件到用户 systemd 目录
cp bilibot.service ~/.config/systemd/user/bilibot.service
systemctl --user daemon-reload

# 允许用户服务开机自启（仅首次，需要一次 sudo）
sudo loginctl enable-linger $USER
```

### 5. 启动守护模式

```bash
# 启动服务
systemctl --user start bilibot

# 设置开机自启
systemctl --user enable bilibot

# 查看日志
journalctl --user -u bilibot -f
```

## 配置说明

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | 是 |
| `TAVILY_API_KEY` | Tavily 联网搜索 Key（未配置则只用 DuckDuckGo） | 否 |
| `BILIBILI_REFRESH_TOKEN` | Cookie 自动刷新 Token（从浏览器 localStorage 获取） | 否 |

### Cookie 与身份认证

Bot 通过 `config/bilibili-cookies.txt` 中的 Netscape 格式 Cookie 维持 B站登录态。以下说明 Cookie 的获取、自动刷新和手动维护流程。

#### 初次获取 Cookie

在 WSL/Ubuntu 环境下，通过 Playwright 打开本地 Chrome 登录 B站：

1. 启动 Playwright CDP 浏览器，导航到 `bilibili.com`
2. 在浏览器中手动完成登录（扫码/密码）
3. 从浏览器 DevTools → Application → Cookies 中提取以下关键 Cookie：
   - `SESSDATA`（httpOnly，无法用 JS 读取，必须从 DevTools 手动复制）
   - `bili_jct`（CSRF Token，用于写操作）
   - `DedeUserID`（当前登录 UID）
   - `buvid3`、`buvid4`、`b_nut` 等辅助 Cookie
4. 保存为 Netscape 格式到 `config/bilibili-cookies.txt`
5. 从浏览器 Console 执行 `localStorage.getItem("ac_time_value")` 获取 Refresh Token
6. 将 Refresh Token 写入 `.env`：`BILIBILI_REFRESH_TOKEN=<获取到的值>`

#### Refresh Token 自动刷新机制

Bot 每 30 分钟检查一次 Cookie 健康状态，检查通过后判断是否需要刷新。完整流程：

```
check_health()
  ├─ GET /x/web-interface/nav        → 验证登录态（code=0 表示有效）
  ├─ GET /x/passport-login/web/cookie/info → 获取 refresh 标志 + timestamp
  └─ 返回 CookieHealth(valid, should_refresh, timestamp_ms)

maybe_refresh()（仅当 should_refresh=true 时触发）
  ├─ 1. RSA-OAEP 加密 "refresh_{timestamp}" → correspond_path
  ├─ 2. GET /bilibili.com/correspond/1/{correspond_path}
  │      → 从 HTML <div id="1-name"> 提取 refresh_csrf
  ├─ 3. POST /x/passport-login/web/cookie/refresh
  │      data: { csrf, refresh_csrf, source:"main_web", refresh_token }
  │      → 响应 Set-Cookie 写回 config/bilibili-cookies.txt
  └─ 4. POST /x/passport-login/web/confirm/refresh
         data: { csrf, refresh_token }（使用旧的 refresh_token）
         → 确认刷新完成
```

**关键参数**：
- `correspond_path`：通过 B站 RSA 公钥（OAEP+SHA256）加密 `refresh_{timestamp}` 生成，每次刷新唯一
- `refresh_csrf`：从 correspond 页面 HTML 中正则提取，有效期短
- `csrf`：即 `bili_jct`，从当前 Cookie 中获取

**⚠️ 重要**：刷新成功后 API 会返回新的 `refresh_token`，Bot 会在日志中打印前 8 位。但 **Bot 不会自动更新 `.env` 文件**，需要手动将新的 refresh_token 写入 `.env` 的 `BILIBILI_REFRESH_TOKEN`。否则下次刷新会因 token 过期而失败。

#### 手动维护场景

| 场景 | 症状 | 操作 |
|------|------|------|
| Cookie 过期 | 所有 API 返回 `code=-101` | 重新登录提取 Cookie，覆盖 `config/bilibili-cookies.txt` 和 opencode cookies |
| Refresh Token 过期 | 日志显示 `刷新失败` 或 cookie 自动刷新不生效 | 重新登录获取新 Refresh Token，写入 `.env` |
| Cookie 文件与脚本不同步 | opencode 脚本（发私信、查视频）返回 `code=-101` | `cp config/bilibili-cookies.txt /home/shf/.config/opencode/docs/bilibili-msg/bilibili-cookies.txt` |

### 主要配置

完整 `config/bot-config.toml` 字段参考：

```toml
[bot]
enabled = true                     # 是否启用
poll_interval_seconds = 5          # 主循环间隔
log_level = "INFO"                 # 日志级别（DEBUG/INFO/WARNING/ERROR）
request_timeout_seconds = 25       # 所有 HTTP 请求超时
source_failure_cooldown_seconds = 180  # 来源熔断冷却时间
report_enabled = true               # 启用每日统计报告
report_owner_uid = "363098992"      # 报告接收者 UID
report_hour = 0                     # 报告发送时间（0=午夜）

[sources.msgfeed]
enabled = true                     # 消息通知回复源（主回复源，覆盖所有评论回复）
poll_interval_seconds = 8
page_size = 10

[sources.mention]
enabled = true                     # @我 消息源
page_size = 10

[sources.own_video]
enabled = true                     # 自己视频新评论源
video_page_size = 5                # 最多检查多少个视频
comment_page_size = 10             # 每个视频拉多少评论

[sources.own_dynamic]
enabled = true                     # 自己动态新评论源
dynamic_page_size = 5
comment_page_size = 10

[sources.dm]
enabled = true                     # 私信自动回复
poll_interval_seconds = 5
max_reply_per_round = 5            # 每轮最多回复几个对话

[filters]
skip_self = true                   # 不回复自己
skip_empty = true                  # 跳过空评论
skip_pure_emoji = true             # 跳过纯颜文字评论
min_meaningful_length = 2          # 最短有意义的评论长度（中文字数）
blacklist_mids = []                # 黑名单用户 UID 列表
followed_only = false              # 设为 true 则只回复关注了 bot 的用户

[reply]
system_prompt_file = "config/system-prompt.txt"  # 从独立文件加载角色 Prompt
temperature = 0.5                  # LLM 温度（0=确定性，1=创造性）
max_tokens = 800                   # 最大生成 token 数

[ai]
primary_provider = "deepseek"      # 主 AI Provider
timeout_seconds = 25               # AI 调用超时
max_reply_chars = 100              # 评论回复字数上限（工具调用时可延长）
tools_enabled = true               # 启用 Function Calling
tool_max_iterations = 3            # 工具调用最大迭代次数
search_quota_daily = 30             # 搜索次数每日上限

[ai.providers.deepseek]
type = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"

[rate_limit]
min_request_interval_seconds = 0.5     # 最小请求间隔
reply_delay_min_seconds = 0.3          # 回复前最小延迟
reply_delay_max_seconds = 1.2          # 回复前最大延迟
max_retries = 5                        # 最大重试次数
backoff_base_seconds = 10              # 重试退避基数
circuit_breaker_failures = 5           # 连续失败多少次触发全局熔断
circuit_breaker_cooldown_seconds = 600 # 全局熔断冷却时间
max_hourly_replies = 20               # 每小时最多回复数
max_daily_replies = 100               # 每天最多回复数
max_replies_per_user_per_hour = 5     # 单用户每小时上限
max_replies_per_oid_per_hour = 10     # 单内容（视频/动态）每小时上限
source_circuit_breaker_failures = 3   # 单来源连续失败多少次触发来源熔断

[cookie]
cookies_file = "config/bilibili-cookies.txt"
refresh_enabled = true                # 是否启用自动刷新
refresh_token_env = "BILIBILI_REFRESH_TOKEN"
check_interval_minutes = 30           # Cookie 健康检查间隔
healthcheck_endpoint = "https://api.bilibili.com/x/web-interface/nav"

[content_safety]
sensitive_words = ["赌博", "色情", ...]  # 敏感词黑名单
max_length = 500                       # 回复最大字数
max_url_count = 3                      # 回复中最多允许的链接数
block_pii = true                       # 阻止含手机号/身份证/邮箱等个人信息的回复
```

## 用户使用方式

**Bot 身份**：UID `3706984385742849`，昵称「小苏doge」。在评论区 @小苏doge、在动态/专栏下 @bot、或在 B站私信直接发消息即可触发回复。

**视频总结**：在任何 B站视频评论区 @bot 账号，发送 "总结一下" 或 "这视频讲了什么"。

**动态/文章评论**：在任何动态或专栏文章下 @bot，Bot 自动识别内容类型并提取标题/摘要作为上下文。图文动态会识别图片数量。

Bot 自动调用 `get_video_content` 工具：
1. 先尝试 B站 AI 摘要（2-10s）
2. 不可用时自动降级为 Whisper 语音转录（30-90s）
3. 转录结果缓存，同一视频不重复转录

**联网搜索**：在私信或评论中问 "今天有什么新闻"、"查一下XX"，Bot 自动调用 `search_web` 工具，Tavily 搜索优先（每日配额 30 次），配额耗尽自动降级 DuckDuckGo。

## 数据文件

`data/` 目录下的运行时文件全部 `.gitignore`，说明如下：

| 文件 | 格式 | 用途 | 可以删除吗 | 维护 |
|------|------|------|-----------|------|
| `processed.jsonl` | JSONL | 事件去重记录。每条处理过的事件（成功/失败/跳过）都记一行 | ❌ **删了会丢失去重状态**，已回复过的评论可能被重复回复 | Bot 每天自动检查：文件 >10MB 或 >5000 条时自动 compact 去重。也可手动 `StateStore().compact_processed()` |
| `reply-history.jsonl` | JSONL | 回复历史（仅成功回复，用于审计） | ✅ 可以删除，只影响历史追溯 | 保留最近 10000 条，自动裁剪 |
| `bot-state.json` | JSON | 运行时状态：`rate_limit`（频控计数/熔断状态）、`source_last_run`（各来源上次运行时间戳）、`cookie_health`（Cookie 健康状态）、`comment_contexts`（评论连续对话历史） | ❌ **删了会丢失频控状态和对话历史**，可能短时间大量回复 | 可手动改 `cooldown_until=0` 解除熔断 |
| `search_quota.json` | JSON | 每日搜索配额 `{"day":"2026-05-11","count":12}` | ✅ 可以删除或把 count 改为 0 重置配额 | 次日自动重置 |
| `feedback.jsonl` | JSONL | 回复质量反馈数据（点赞数、回复数） | ✅ 可以删除，只影响质量统计 | 每 6 小时检查一次，保留最近 7 天数据 |

> 上下文富化用的数据（视频标题、父评论、私信历史）**不存储在任何文件中**，每次从 B站 API 实时获取，用完即弃。
> **例外**：评论连续对话历史（`comment_contexts`）会持久化到 `bot-state.json`，用于追踪同一用户在同一视频下的连续对话。

## 目录结构

```
bilibili-bot/
├── src/bilibili_bot/          # 源代码
│   ├── __main__.py           # 入口（daemon 循环 + 主流程）
│   ├── config.py             # Pydantic 配置（所有配置模型）
│   ├── client.py             # HTTP 客户端（WBI 签名 + Cookie注入）
│   ├── events.py             # 事件模型（CommentEvent / DMEvent）
│   ├── state.py              # 状态存储（带文件锁，JSONL+JSON）
│   ├── cookie.py             # Cookie 刷新管理（RSA-OAEP + refresh_csrf）
│   ├── wbi.py                # WBI 签名算法
│   ├── log.py                # 结构化日志配置（structlog）
│   ├── stats.py              # 每日统计报告生成
│   ├── auto_skip.py          # 已知问题用户自动跳过
│   ├── feedback.py           # 回复质量反馈追踪（点赞/回复数）
│   ├── pipeline/             # 处理管道
│   │   ├── base.py           # PipelineStage ABC + Pipeline
│   │   ├── dedup.py          # 去重（新/已回复/已见/失败重试/致命错误）
│   │   ├── filter.py         # 过滤（自己/空/黑名单/纯颜文字/过短/仅关注）
│   │   ├── rate_limit.py     # 频控 + 熔断（小时/日/用户/OID 多维度）
│   │   ├── generate.py       # AI 生成（PydanticAI Agent + Provider 降级）
│   │   ├── safety.py         # 内容安全审查（敏感词/PII/链接/长度）
│   │   └── send.py           # 发送（评论 WBI + 私信 WBI 签名）
│   ├── providers/            # AI Provider
│   │   ├── base.py           # Provider ABC + ReplyResult
│   │   ├── openai_compat.py  # OpenAI 兼容 Provider（generate + PydanticAI Agent 桥接）
│   │   └── manager.py        # Provider 管理
│   ├── tools/                # PydanticAI Tool 工具系统
│   │   ├── __init__.py       # Tool 定义 + 实现（PydanticAI Tool 包装器）
│   │   ├── transcribe.py     # Whisper 语音转录（yt-dlp + faster-whisper）
│   │   └── web_search.py     # 联网搜索（Tavily + DuckDuckGo 降级）
│   └── sources/              # 数据来源
│       ├── base.py           # Source ABC
│       ├── msgfeed.py        # 消息通知回复 + bvid 补充 + 用户画像
│       ├── mention.py        # @我消息（复用 msgfeed enrich 逻辑）
│       ├── own_video.py      # 自己视频评论（带重试）
│       ├── own_dynamic.py    # 自己动态评论（视频/图文）
│       └── dm.py             # 私信（会话列表 + 历史消息 + 分享解析）
├── scripts/                  # 辅助脚本
│   └── bilibili_wbi.py       # WBI 签名 + AI 摘要 + 用户信息 + 用户搜索
├── models/whisper/           # Whisper 模型（gitignored）
├── config/                   # 配置文件
│   ├── system-prompt.txt     # 角色 Prompt（独立维护）
│   ├── bot-config.toml       # 机器人完整配置
│   ├── bilibili-cookies.txt  # B站 Cookie（Netscape 格式，gitignored）
│   └── bilibili-cookies.example.txt
├── data/                     # 运行时数据（gitignored）
├── tests/                    # 单元测试（31 个）
├── bilibot.service           # systemd 服务文件
├── pyproject.toml
└── README.md
```

## 架构设计

### Pipeline 管道

```
Source.fetch() → [Event]
    ↓
DedupStage      → 跳过已处理（失败重试最多 5 次，冷却 5 分钟；致命错误 1 小时后过期）
FilterStage     → 跳过自己/空/黑名单/纯颜文字/过短/非关注（DM pipeline 跳过此阶段）
RateLimitStage  → 频控检查 + 等待随机延迟
GenerateStage   → PydanticAI Agent 带工具调用生成（自定义 loop → PydanticAI agent.run_sync）→ 失败降级 Provider.generate()
SafetyStage     → 敏感词/PII/链接/长度四重检查
SendStage       → 发送到 Bilibili API（评论 WBI 签名 + 私信 WBI 签名）
```

### Tool Calling 流程（PydanticAI）

```
用户评论 "总结一下这个视频"
    ↓
GenerateStage._generate_reply_with_tools()
    ↓
PydanticAI Agent
    ├─ _create_pydantic_agent(system_prompt, config, provider)
    ├─ agent.run_sync(user_prompt, message_history, model_settings, usage_limits)
    ├─ 最多 tool_max_iterations 轮工具调用
    └─ result.output → ReplyResult(text=..., tool_calls=[...])
        │
        ├─ 成功 → 返回回复
        └─ 异常 → 降级 Provider.generate_reply()（纯 LLM 回复，无工具）
```

**工具定义**（`tools/__init__.py`）：
```python
def get_video_content(bvid: str) -> str: ...
    # AI 摘要 → 失败 → Whisper 转录 → 失败 → 错误提示

def search_web(query: str) -> str: ...
    # Tavily → 配额耗尽/失败 → DuckDuckGo → 失败 → 错误提示

TOOLS = [
    Tool(_with_tool_logging(get_video_content)),
    Tool(_with_tool_logging(search_web)),
]
```

**PydanticAI vs 旧方案**：
- 旧：手写 ~60 行 tool calling 循环（`openai_compat.py` 的 `generate_with_tools`），手动管理 messages、tool_calls 解析、reasoning_content 回传、迭代控制
- 新：PydanticAI `Agent` 一行 `agent.run_sync()` 完成规划-执行-输出全流程，内置 OpenAI-compatible HTTP 客户端，原生支持 DeepSeek V4 Flash
- 降级：Agent 异常时自动降级为 `ProviderManager.generate_reply()`（纯 LLM 回复，无工具）

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
    video_title: str        # 视频标题/动态内容/文章标题（自动注入）
    parent_content: str     # 父评论内容（楼中楼上下文，msgfeed/mention 源自动提取）
    at_me: bool             # 是否 @了 bot
    images: list[str]       # 动态图片 URL（为视觉模型预留）
    
    # 用户画像扩展
    author_level: int = 0   # 用户等级 (0-6)
    author_fans_count: int = 0  # 粉丝数
    interaction_count: int = 0  # 历史互动次数
    
    # 视频热度扩展
    video_view_count: int = 0   # 播放量
    video_like_count: int = 0   # 点赞数
    video_coin_count: int = 0   # 投币数
    video_favorite_count: int = 0  # 收藏数
    video_share_count: int = 0  # 分享数
    video_reply_count: int = 0  # 评论数
    up_name: str = ""       # UP主名称
    up_fans_count: int = 0  # UP主粉丝数
    
    # 连续对话扩展
    recent_replies: list = field(default_factory=list)  # 历史回复
    conversation_summary: str = ""  # 对话摘要

@dataclass
class DMEvent(Event):
    talker_id: int          # 对话对方 UID
    talker_name: str        # 对方昵称
    dm_content: str         # 私信内容
    recent_messages: list   # 最近对话历史（15条，每轮从60条中取最新15条）
```

### 上下文富化

Bot 在生成 AI 回复前，会从 B站 API 实时获取额外信息注入 prompt，让 LLM 更了解对话背景。**所有上下文仅存在于内存，拼好 prompt 发给 DeepSeek 后立即丢弃，不写磁盘，不跨轮次记忆。**

#### 评论场景

```
[发给 DeepSeek 的 prompt]
当前时间：2026年05月09日 19:30
来源：视频
内容标题：DeepSeek V4 发布，OpenAI 慌了        ← 从 /x/web-interface/view 实时拉
视频BV号：BV1xx411c7mD                        ← aid 自动转换
视频简介：梁文峰在知乎上透露...（200字截断）     ← 仅 msgfeed/mention 源
对话上下文：→ 回复 老王：这比V3强在哪           ← 仅 own_video/dynamic 源（楼中楼）
注：对方是你的粉丝                              ← 从 /x/space/wbi/acc/info 查
用户等级：Lv6                                  ← 从 /x/space/wbi/acc/info 查
粉丝数：1234                                   ← 从 /x/space/wbi/acc/info 查
历史互动次数：5                                 ← 从 bot-state.json 的 comment_contexts 统计
视频播放量：100000                              ← 从 /x/web-interface/view 获取
视频点赞数：5000                                ← 从 /x/web-interface/view 获取
视频收藏数：2000                                ← 从 /x/web-interface/view 获取
UP主：测试UP主                                 ← 从 /x/web-interface/view 获取
UP主粉丝数：50000                              ← 从 /x/space/wbi/acc/info 查
对话背景摘要：用户之前问过好                     ← 从 bot-state.json 的 comment_contexts 获取
历史对话：                                      ← 从 bot-state.json 的 comment_contexts 获取
  对方：你好
  我：你好呀！
被回复的评论：开源真香（200字截断）               ← 楼中楼父评论
是否@我：是
评论作者：小王同学
评论内容：总结一下这个视频

请直接生成回复。
```

**上下文来源**：
- `video_title` / `bvid` / `video_desc` / `video_view_count` / `video_like_count` / `video_favorite_count` / `up_name` → `MsgFeedReplySource._enrich_events()` 调用 `/x/web-interface/view`
- `parent_content` / `thread_context` → 来源 API 返回的 `parent_reply` 字段
- `author_follower` / `author_level` / `author_fans_count` → `MsgFeedReplySource._enrich_users()` 调用 `/x/space/wbi/acc/info`
- `interaction_count` / `recent_replies` / `conversation_summary` → 从 `bot-state.json` 的 `comment_contexts` 获取

#### 私信场景

```
[system] 你是小苏doge...
[user]   你好呀（第1条，200字截断）
[assistant] 你好！有什么事（bot回复）
[user]   你叫什么名字（第2条）
...（共15条，user/bot交替）
[user]   用户 小张 发来私信：你会写代码吗？       ← 最新一条
```

每次从 B站私信 API 拉 **60 条**历史，取最近 **15 条**注入 prompt，每条截断到 **200 字**。user 和 bot 消息交替排列，给 LLM 完整的对话上下文。

## 风控策略

- **随机延迟** - 每条回复前等待 0.3-1.2s
- **来源熔断** - 单源连续失败 3 次 → 冷却 180s
- **全局熔断** - 连续失败 5 次 → 冷却 600s
- **去重冷却** - 失败重试最多 5 次，冷却 5 分钟，致命错误 1 小时后过期
- **小时上限** - 每小时最多 20 条
- **日上限** - 每天最多 100 条
- **转录冷却** - 两次语音转录间隔 30s

## 统计与反馈

### 每日统计报告

Bot 每天 0:00 自动生成统计报告，通过私信发送给主人（UID `363098992`）：

```
📊 今日报告
────────────────────
评论回复: 15 条 | 私信: 4 条 | 总计: 19 条
工具调用: 视频总结 2 次 | 搜索 1 次
来源分布: dm:4  msgfeed:10  mention:5
─
API 调用估算: ~21 次
Token 估算: ~19.0k
搜索配额: 12/30（18 剩余）
错误: 0 次
```

如某来源被熔断冷却中，会附加一行 `⚠️ MsgFeedReplySource 冷却中(180s)`。

配置项：`[bot]` 段 `report_enabled` / `report_owner_uid` / `report_hour`。

### 自动跳过

同一用户在 24 小时内产生 3 次致命错误（如评论已删除、内容不存在），Bot 自动跳过该用户后续的同类事件，避免浪费 AI API 调用和 B站接口配额。状态持久化在 `bot-state.json` 中，过期自动清理。

### 多轮对话记忆

私信中对话超过 30 条时，Bot 自动调用 LLM 将最老的对话总结为一句话摘要，注入到当前对话的上下文中。短对话不触发，不增加额外 API 调用。

### 回复质量反馈

Bot 每 6 小时检查一次最近 7 天评论的点赞数和回复数，存入 `data/feedback.jsonl`。数据可用于统计日报中的回复质量指标。每次最多检查 10 条，失败不影响主循环。

## 管理命令

```bash
# systemd（推荐）
systemctl --user status bilibot     # 状态
systemctl --user restart bilibot    # 重启
journalctl --user -u bilibot -f     # 实时日志

# 单轮测试
.venv/bin/python3 -m bilibili_bot --once --dry-run
.venv/bin/python3 -m bilibili_bot --once
```

## 常见问题排查

### Bot 不回复了

按顺序排查：

```
1. systemctl --user status bilibot     → 确认 Active: active (running)
2. journalctl --user -u bilibot -p err -n 30 → 找错误
```

常见原因与修复：

| 症状 | 原因 | 修复 |
|------|------|------|
| 日志全是 `code=-101` | **Cookie 过期** | 重新登录提取 Cookie，覆盖 `config/bilibili-cookies.txt`，重启 |
| 日志有 `generate_failed` | **DeepSeek API 欠费/限流** | 检查 DeepSeek 余额，更新 `.env` 中的 `DEEPSEEK_API_KEY` |
| 日志正常但没有 `send_success` | **频控触发** 或 **熔断中** | `cat data/bot-state.json` 查 `rate_limit.failure_count` 和 `cooldown_until`，等冷却或手动归零 |
| 私信发不出去 | **缺少 WBI 签名** 或 `bili_jct` 缺失 | 检查 Cookie 文件包含 `bili_jct` 字段 |
| 某个事件反复出现却不回复 | **事件被 dedup 永久封死**（已失败 ≥5 次） | `grep -v "事件event_key" data/processed.jsonl > /tmp/clean && mv /tmp/clean data/processed.jsonl`，重启 |
| journalctl 为空 | **日志级别过高** | 检查 `config/bot-config.toml` 的 `log_level = "INFO"` |

### 运行测试

```bash
cd /home/shf/bilibili-bot
PYTHONPATH=src .venv/bin/pytest tests/ -v
```

### 验证工具链

```bash
cd /home/shf/bilibili-bot && source .env

# 验证 PydanticAI 工具链
.venv/bin/python3 -c "
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from bilibili_bot.tools import TOOLS
import os

provider = OpenAIProvider(
    base_url='https://api.deepseek.com/v1',
    api_key=os.environ['DEEPSEEK_API_KEY'],
)
model = OpenAIChatModel('deepseek-v4-flash', provider=provider)
agent = Agent(model, system_prompt='用中文回复', tools=TOOLS)
result = agent.run_sync('回复你好')
print(result.output[:50])
"

# 验证 openai_compat Provider 生成
.venv/bin/python3 -c "
from bilibili_bot.config import BotConfig
from bilibili_bot.providers.openai_compat import OpenAICompatibleProvider
c = BotConfig.from_toml('config/bot-config.toml')
p = OpenAICompatibleProvider('t', c.ai.providers['deepseek'].model_dump(), c)
r = p.generate([{'role':'user','content':'回复你好'}])
print(f'success={r.success} text={r.text[:50]}')
"

# 验证 WBI 签名 + AI 摘要
.venv/bin/python3 scripts/bilibili_wbi.py BV1xx411c7mD config/bilibili-cookies.txt

# 验证 Cookie 健康
.venv/bin/python3 -c "
from bilibili_bot.config import BotConfig
from bilibili_bot.cookie import CookieRefreshManager
c=BotConfig.from_toml('config/bot-config.toml')
s=CookieRefreshManager(c).check_health()
print(f'valid={s.valid} refresh={s.should_refresh} {s.message}')
"
```

## 许可证

MIT
