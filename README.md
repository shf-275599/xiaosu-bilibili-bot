# Bilibili 评论自动回复机器人

自动监听 Bilibili 评论和私信，使用 AI 生成回复并发送的后台守护进程。

## 功能特性

- **多来源监听** - 消息通知回复、@我消息、自己动态评论、自己视频评论
- **私信自动回复** - 自动检测未读私信并生成 AI 回复
- **AI 智能回复** - 支持 OpenAI-compatible API（Deepseek/GPT/Claude 等）+ 本地降级通道
- **Cookie 自动刷新** - RSA-OAEP 加密 + refresh_csrf 完整链路
- **QR 码自动登录** - 无 refresh_token 时自动启动 QR 码登录，扫码即可获取
- **保守风控** - 随机延迟、来源熔断、全局熔断、小时/日回复上限
- **历史去重** - 跨来源 `(business_type, oid, rpid)` 唯一键去重
- **状态持久化** - JSONL 格式记录处理历史和回复日志

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
# 复制配置模板
cp .env.example .env
cp config/bilibili-cookies.example.txt config/bilibili-cookies.txt

# 编辑配置文件
nano .env                          # 填入 API Key 和 Refresh Token
nano config/bilibili-cookies.txt   # 填入真实 Cookies
nano config/bot-config.toml        # 调整机器人配置
```

### 3. 测试运行

```bash
# 查看当前消息流（只读）
python3 src/bilibili_bot/bilibili-comment-bot.py --print-msgfeed

# 执行一轮 dry-run（生成回复但不发送）
python3 src/bilibili_bot/bilibili-comment-bot.py --once --dry-run

# 执行一轮真实自动回复
python3 src/bilibili_bot/bilibili-comment-bot.py --once
```

### 4. 启动守护模式

```bash
# 前台运行
python3 src/bilibili_bot/bilibili-comment-bot.py

# 后台运行（推荐使用 tmux）
tmux new-session -d -s bilibot
python3 src/bilibili_bot/bilibili-comment-bot.py
```

## 配置说明

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（主 AI Provider） | 是 |
| `BILIBILI_REFRESH_TOKEN` | Bilibili Refresh Token（Cookie 自动刷新） | 否 |
| `BILIBILI_BOT_ROOT` | 项目根目录（默认自动检测） | 否 |

### 获取 Refresh Token

1. 用浏览器登录 [https://www.bilibili.com](https://www.bilibili.com)
2. 按 F12 打开开发者工具
3. 切换到 **Application** → **Local Storage** → `https://www.bilibili.com`
4. 查找 `ac_time_value` 键，复制其值

### 配置文件

编辑 `config/bot-config.toml`：

```toml
[bot]
poll_interval_seconds = 30    # 轮询间隔
log_level = "INFO"            # 日志级别

[sources.msgfeed]
enabled = true                # 启用消息通知回复监听

[sources.mention]
enabled = true                # 启用 @我 消息监听

[sources.dm]
enabled = true                # 启用私信自动回复
poll_interval_seconds = 60    # 私信轮询间隔
max_reply_per_round = 5       # 每轮最大回复数
skip_keywords = ["广告", "推广"]  # 跳过含关键词的私信
whitelist_mids = []           # 白名单用户 UID（空=全部回复）

[ai]
primary_provider = "deepseek" # 主 AI Provider
fallback_provider = "local"   # 降级 Provider

[reply]
system_prompt = "你是一个友善的B站UP主..."
temperature = 0.75            # 回复随机性（0-1）
max_tokens = 200              # 最大 token 数

[dm_reply]
system_prompt = "你是一个友善的B站用户..."
temperature = 0.7
max_tokens = 200

[rate_limit]
max_hourly_replies = 20       # 每小时最大回复数
max_daily_replies = 100       # 每天最大回复数
```

## 目录结构

```
bilibili-bot/
├── src/bilibili_bot/          # 源代码
│   ├── bilibili-comment-bot.py   # 主入口
│   ├── bot_config.py             # 配置加载
│   ├── comment_sources.py        # 多来源采集
│   ├── comment_normalizer.py     # 事件归一化
│   ├── comment_filters.py        # 过滤规则
│   ├── comment_dedup.py          # 去重服务
│   ├── comment_sender.py         # 评论发送
│   ├── dm_source.py              # 私信轮询
│   ├── dm_sender.py              # 私信发送
│   ├── dm_dedup.py               # 私信去重
│   ├── dm_prompt.py              # 私信 Prompt
│   ├── reply_providers.py        # AI Provider
│   ├── reply_prompt.py           # Prompt 构建
│   ├── rate_control.py           # 风控熔断
│   ├── cookie_refresh.py         # Cookie 刷新
│   ├── qr_login.py               # QR 码登录
│   ├── state_store.py            # 状态持久化
│   └── bilibili_wbi.py           # WBI 签名
├── config/                    # 配置文件
│   ├── bot-config.toml          # 机器人配置
│   ├── bilibili-cookies.txt     # Cookies（不提交 git）
│   └── bilibili-cookies.example.txt
├── data/                      # 运行时数据（不提交 git）
│   ├── bot-state.json           # 运行状态
│   ├── processed-comments.jsonl # 去重记录
│   └── reply-history.jsonl      # 回复历史
├── docs/                      # 文档
├── .env.example               # 环境变量模板
├── .gitignore
├── requirements.txt
└── README.md
```

## CLI 参数

| 参数 | 说明 |
|------|------|
| `--config PATH` | 指定配置文件路径（默认 `config/bot-config.toml`） |
| `--once` | 只执行一轮，不进入守护模式 |
| `--dry-run` | 只生成回复，不实际发送 |
| `--print-msgfeed` | 打印当前消息流事件（只读） |

## 风控策略

- **随机延迟** - 每条回复前随机等待 8-20 秒
- **来源熔断** - 单来源连续失败 3 次 → 冷却 180 秒
- **全局熔断** - 连续失败 5 次 → 冷却 600 秒
- **小时上限** - 每小时最多 20 条回复
- **日上限** - 每天最多 100 条回复

## 自定义 AI Provider

在 `config/bot-config.toml` 中添加新的 Provider：

```toml
[ai.providers.openai]
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
model = "gpt-4"
api_key_env = "OPENAI_API_KEY"

[ai.providers.claude]
type = "openai_compatible"
base_url = "https://api.anthropic.com/v1"
model = "claude-3-sonnet-20240229"
api_key_env = "ANTHROPIC_API_KEY"
```

然后修改 `primary_provider` 或 `fallback_provider` 为新 Provider 名称。

## 自定义回复 Prompt

编辑 `config/bot-config.toml` 中的 `[reply]` 部分：

```toml
[reply]
system_prompt = """
你是一个技术博主，擅长编程和 AI。
回复要求：
- 简短自然，不超过 80 字
- 技术问题认真回答
- 日常评论轻松幽默
- 不要像机器人
"""
temperature = 0.8
max_tokens = 150
```

## 常见问题

### Cookie 失效怎么办？

机器人会自动检测 Cookie 失效并停止发送。如果配置了 `BILIBILI_REFRESH_TOKEN`，会自动刷新。否则需要手动更新 `config/bilibili-cookies.txt`。

### 如何查看日志？

```bash
# 前台运行时直接看输出
# 后台运行时查看 tmux 会话
tmux attach -t bilibot
```

### 如何停止机器人？

```bash
# 如果是前台运行，按 Ctrl+C
# 如果是 tmux 后台运行
tmux kill-session -t bilibot
```

## 许可证

MIT
