# Bilibili 评论自动回复机器人 v2

自动监听 Bilibili 评论和私信，使用 DeepSeek V4 Flash 生成 AI 回复的后台守护进程。

## 功能特性

- **Pipeline 管道架构** - 模块化处理流程（dedup → filter → rate_limit → generate → safety → send）
- **多来源监听** - 消息通知回复、@我消息、自己动态评论、自己视频评论、私信
- **AI 智能回复** - DeepSeek V4 Flash，支持 Function Calling（Tool Calling）
- **上下文富化** - 自动携带视频标题、父评论内容、私信对话历史、BV号
- **视频内容工具** - 用户 @bot 可获取视频 AI 摘要，不可用时自动降级为 Whisper 语音转录
- **联网搜索** - Tavily 优先（月度配额），自动降级到 DuckDuckGo
- **Cookie 自动刷新** - RSA-OAEP 加密 + refresh_csrf 完整链路
- **DM 私信回复** - 监听私信，AI 生成回复并发送（含 WBI 签名）
- **保守风控** - 随机延迟、来源熔断、全局熔断、小时/日回复上限
- **类型安全配置** - Pydantic v2 配置验证
- **结构化日志** - structlog JSON 格式，便于监控

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
skip_keywords = ["广告", "推广"]    # 包含这些词的私信不回复

[filters]
skip_self = true                   # 不回复自己
skip_empty = true                  # 跳过空评论
skip_pure_emoji = true             # 跳过纯颜文字评论
min_meaningful_length = 2          # 最短有意义的评论长度（中文字数）
blacklist_mids = []                # 黑名单用户 UID 列表

[reply]
system_prompt = "你是小苏doge..."   # 系统 Prompt（角色扮演 + 回复风格）
temperature = 0.75                 # LLM 温度（0=确定性，1=创造性）
max_tokens = 800                   # 最大生成 token 数

[ai]
primary_provider = "deepseek"      # 主 AI Provider
timeout_seconds = 25               # AI 调用超时
max_reply_chars = 100              # 评论回复字数上限（工具调用时可延长）
tools_enabled = true               # 启用 Function Calling
tool_max_iterations = 3            # 工具调用最大迭代次数
search_quota_monthly = 30          # Tavily 每月搜索次数上限

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

**Bot 身份**：UID `3706984385742849`，昵称「小苏doge」。在评论区 @小苏doge 或在 B站私信直接发消息即可触发回复。

**视频总结**：在任何 B站视频评论区 @bot 账号，发送 "总结一下" 或 "这视频讲了什么"。

Bot 自动调用 `get_video_content` 工具：
1. 先尝试 B站 AI 摘要（2-10s）
2. 不可用时自动降级为 Whisper 语音转录（30-90s）
3. 转录结果缓存，同一视频不重复转录

**联网搜索**：在私信或评论中问 "今天有什么新闻"、"查一下XX"，Bot 自动调用 `search_web` 工具，Tavily 搜索优先（月度配额 30 次），配额耗尽自动降级 DuckDuckGo。

## 数据文件

`data/` 目录下的运行时文件全部 `.gitignore`，说明如下：

| 文件 | 格式 | 用途 | 可以删除吗 | 维护 |
|------|------|------|-----------|------|
| `processed.jsonl` | JSONL | 事件去重记录。每条处理过的事件（成功/失败/跳过）都记一行 | ❌ **删了会丢失去重状态**，已回复过的评论可能被重复回复 | 过大会拖慢加载，用 `python -c "from bilibili_bot.state import StateStore; StateStore().compact_processed()"` 清理已成功记录 |
| `reply-history.jsonl` | JSONL | 回复历史（仅成功回复，用于审计） | ✅ 可以删除，只影响历史追溯 | 保留最近 10000 条，自动裁剪 |
| `bot-state.json` | JSON | 运行时状态：`rate_limit`（频控计数/熔断状态）、`source_last_run`（各来源上次运行时间戳）、`cookie_health`（Cookie 健康状态） | ❌ **删了会丢失频控状态**，可能短时间大量回复 | 可手动改 `cooldown_until=0` 解除熔断 |
| `search_quota.json` | JSON | Tavily 搜索月度配额 `{"month":"2026-05","count":12}` | ✅ 可以删除或把 count 改为 0 重置配额 | 月初自动重置 |

> 上下文富化用的数据（视频标题、父评论、私信历史）**不存储在任何文件中**，每次从 B站 API 实时获取，用完即弃。

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
DedupStage      → 跳过已处理（失败重试最多 5 次，冷却 5 分钟；致命错误 1 小时后过期）
FilterStage     → 跳过自己/空/黑名单/纯颜文字/过短（DM pipeline 跳过此阶段）
RateLimitStage  → 频控检查 + 等待随机延迟
GenerateStage   → DeepSeek 生成回复（含 Tool Calling）
SafetyStage     → 敏感词/PII/链接/长度四重检查
SendStage       → 发送到 Bilibili API（评论 WBI 签名 + 私信 WBI 签名）
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
    recent_messages: list   # 最近对话历史（10条，每轮从40条中取最新10条）
```

### 上下文富化

Bot 在生成 AI 回复前，会从 B站 API 实时获取额外信息注入 prompt，让 LLM 更了解对话背景。**所有上下文仅存在于内存，拼好 prompt 发给 DeepSeek 后立即丢弃，不写磁盘，不跨轮次记忆。**

#### 评论场景

```
[发给 DeepSeek 的 prompt]
来源：视频
内容标题：DeepSeek V4 发布，OpenAI 慌了        ← 从 /x/web-interface/view 实时拉
视频BV号：BV1xx411c7mD                        ← aid 自动转换
视频简介：梁文峰在知乎上透露...（200字截断）     ← 仅 msgfeed/mention 源
对话上下文：→ 回复 老王：这比V3强在哪           ← 仅 own_video/dynamic 源（楼中楼）
注：对方是你的粉丝                              ← 从 /x/space/wbi/acc/info 查
被回复的评论：开源真香（200字截断）               ← 楼中楼父评论
评论作者：小王同学
评论内容：总结一下这个视频

请直接生成回复。
```

#### 私信场景

```
[system] 你是小苏doge...
[user]   你好呀（第1条，200字截断）
[assistant] 你好！有什么事（bot回复）
[user]   你叫什么名字（第2条）
...（共10条，user/bot交替）
[user]   用户 小张 发来私信：你会写代码吗？       ← 最新一条
```

每次从 B站私信 API 拉 **40 条**历史，取最近 **10 条**注入 prompt，每条截断到 **200 字**。user 和 bot 消息交替排列，给 LLM 完整的对话上下文。

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

# 验证 AI Provider
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
