# Bilibili 哆啦A梦Bot v4

自动监听 Bilibili 评论和私信，使用 DeepSeek V4 Flash 驱动的哆啦A梦AI Bot。

## 功能

- **视频总结** — 评论区 @ 自动用「见闻镜」阅读并总结视频内容
- **动态/专栏阅读** — 用「XYZ线照相机」透视动态/专栏文字内容
- **联网搜索** — 「巡查电视」Tavily 优先 + DuckDuckGo 降级
- **私信聊天** — PydanticAI Agent 会话级上下文，长对话不丢话题
- **楼中楼** — @在他人评论下时自动提取被回复的评论作为上下文
- **图片** — 能知道有几张图，但透视功能"接触不良"暂时看不到内容
- **多来源** — 消息通知回复、@我、自己视频/动态评论、私信
- **频控防御** — 随机延迟、来源熔断、全局熔断、时/日/单用户/单内容上限
- **Cookie自动刷新** — RSA-OAEP 加密 + refresh_csrf 完整链路
- **仅关注者模式** — 可配置只回复关注者（30分钟自动同步关注列表）
- **安全** — PII检测、URL限制、防注入系统提示词
- **每日统计** — 0点自动推送回复/工具调用/错误统计报告

## 快速开始

### 1. 环境要求

- Python 3.11+、Linux
- B站账号（用于获取 Cookie）
- DeepSeek API Key（当前使用的AI模型）
- （可选）Tavily API Key（联网搜索，否则用DuckDuckGo降级）

### 2. 安装

```bash
git clone https://github.com/shf-275599/bilibili-doraemon-bot.git
cd bilibili-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 联网搜索+语音转录依赖（可选）
pip install ddgs faster-whisper
```

### 3. 配置

```bash
cp .env.example .env
# 编辑 .env，填入：
# DEEPSEEK_API_KEY=你的Key
# BILIBILI_REFRESH_TOKEN=从浏览器localStorage获取
# TAVILY_API_KEY=你的Key（可选）

# 获取B站Cookie（Netscape格式）
# 浏览器登录B站 → F12 → Application → Cookies
# 必需字段: SESSDATA, bili_jct, DedeUserID, buvid3, buvid4
# 保存到 config/bilibili-cookies.txt
cp config/bilibili-cookies.example.txt config/bilibili-cookies.txt
# 然后编辑填入真实值
```

脚本方式获取 Cookie（推荐）：
```bash
pip install playwright
playwright install chromium
python scripts/bilibili_cookie_helper.py  # 自动打开浏览器→登录→导出Cookie
```

### 4. 测试

```bash
# dry-run（生成回复但不发送）
.venv/bin/python3 -m bilibili_bot --once --dry-run

# 单轮真实运行
.venv/bin/python3 -m bilibili_bot --once

# 运行单元测试
.venv/bin/pytest tests/ -v
```

### 5. 部署（systemd）

```bash
cp bilibot.service ~/.config/systemd/user/bilibot.service
systemctl --user daemon-reload
sudo loginctl enable-linger $USER  # 允许用户服务开机自启

systemctl --user start bilibot
systemctl --user enable bilibot
journalctl --user -u bilibot -f     # 查看日志
```

## 配置参考

完整配置见 `config/bot-config.toml`，关键字段：

| 配置段 | 关键字段 | 默认值 | 说明 |
|--------|---------|--------|------|
| `[bot]` | `poll_interval_seconds` | 5 | 主循环间隔(秒) |
| `[bot]` | `report_enabled` | true | 每日统计报告 |
| `[filters]` | `followed_only` | true | 仅回复关注者 |
| `[ai]` | `session_ttl_seconds` | 86400 | 会话过期时间(24h) |
| `[ai]` | `history_max_messages` | 50 | 会话历史上限(条) |
| `[ai]` | `tools_enabled` | true | 启用工具调用 |
| `[reply]` | `system_prompt_file` | — | 角色Prompt文件路径 |
| `[rate_limit]` | `max_hourly_replies` | 20 | 每小时回复上限 |
| `[rate_limit]` | `max_daily_replies` | 100 | 每日回复上限 |

## 架构

```
Source.fetch() → [Event]
    ↓
DedupStage      → 去重(NEW/REPLIED/SEEN/FAILED)
FilterStage     → 过滤(自己/空/黑名单/非关注)
RateLimitStage  → 频控检查 + 随机延迟
GenerateStage   → PydanticAI Agent 会话级生成
SafetyStage     → PII/敏感词/URL/长度检查
SendStage       → Bilibili API (WBI签名)

会话管理:
  DM:  "dm:{talker_id}"                     → 每人独立
  评论: "{source}:{oid}:{mid}"              → 每人每内容独立
  历史: Agent 自动维护, >50条截断到31条      → token控制
  过期: >24h LLM摘要 → 持久化到磁盘 → 重启不丢失
```

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `code=-101` | Cookie过期 | 重新登录提取Cookie |
| `generate_failed` | DeepSeek欠费/限流 | 检查余额/更新API Key |
| 不回复非关注者 | `followed_only=true` | 设为false或等30分钟同步关注列表 |
| 无日志 | `log_level`过高 | 设为 `INFO` |
| DM不回复 | Cookie缺少 `bili_jct` | 检查Cookie文件 |

```bash
# 快捷诊断
journalctl --user -u bilibot -p err -n 20  # 最近错误
cat data/bot-state.json | python3 -m json.tool  # 运行状态
grep "send_success" <(journalctl --user -u bilibot --since 1h ago) | wc -l  # 1小时回复数
```

## star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=shf-275599/bilibili-doraemon-bot&type=Date)](https://star-history.com/#shf-275599/bilibili-doraemon-bot&Date)

## License

MIT
