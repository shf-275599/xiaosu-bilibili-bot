"""配置管理模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import tomllib
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class BotSettings(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 5
    run_mode: str = "daemon"
    log_level: str = "INFO"
    request_timeout_seconds: int = 25
    source_failure_cooldown_seconds: int = 180
    report_enabled: bool = False
    report_owner_uid: str = ""
    report_hour: int = 0


class MsgFeedConfig(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 20
    page_size: int = 10


class MentionConfig(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 30
    page_size: int = 10


class OwnVideoConfig(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 30
    video_page_size: int = 5
    comment_page_size: int = 10
    max_retries: int = 2
    retry_sleep_seconds: int = 6


class OwnDynamicConfig(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 30
    dynamic_page_size: int = 5
    comment_page_size: int = 10


class DMConfig(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 60
    max_reply_per_round: int = 5
    whitelist_mids: list[int] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    msgfeed: MsgFeedConfig = Field(default_factory=MsgFeedConfig)
    mention: MentionConfig = Field(default_factory=MentionConfig)
    own_video: OwnVideoConfig = Field(default_factory=OwnVideoConfig)
    own_dynamic: OwnDynamicConfig = Field(default_factory=OwnDynamicConfig)
    dm: DMConfig = Field(default_factory=DMConfig)


class FilterConfig(BaseModel):
    skip_self: bool = True
    skip_empty: bool = True
    skip_pure_emoji: bool = True
    min_meaningful_length: int = 2
    blacklist_mids: list[int] = Field(default_factory=list)
    followed_only: bool = False


class AIProviderConfig(BaseModel):
    type: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key_env: Optional[str] = None


class AIConfig(BaseModel):
    primary_provider: str = "deepseek"
    timeout_seconds: int = 25
    max_reply_chars: int = 100
    providers: dict[str, AIProviderConfig] = Field(default_factory=dict)
    tools_enabled: bool = True
    tool_max_iterations: int = 3
    search_quota_daily: int = 30
    session_ttl_seconds: int = 3600
    history_max_messages: int = 50


class ReplyConfig(BaseModel):
    system_prompt_file: str = ""
    system_prompt: str = "你是一只小苏doge，一个友善、有梗、说话自然的B站UP主。回复评论时要简短（不超过80字）、接地气、偶尔带点小幽默，不要机械感，不要像客服。看到技术相关可以认真聊，看到玩梗的可以接梗，看到夸你的就谦虚一下。避免重复同样的回复。"
    temperature: float = 0.75
    max_tokens: int = 200

    @model_validator(mode="after")
    def load_prompt_from_file(self) -> ReplyConfig:
        if self.system_prompt_file:
            try:
                file_path = Path(self.system_prompt_file)
                if file_path.exists():
                    self.system_prompt = file_path.read_text(encoding="utf-8")
            except Exception:
                pass
        return self


class RateLimitConfig(BaseModel):
    min_request_interval_seconds: float = 1
    reply_delay_min_seconds: float = 1
    reply_delay_max_seconds: float = 3
    max_retries: int = 3
    backoff_base_seconds: int = 10
    circuit_breaker_failures: int = 5
    circuit_breaker_cooldown_seconds: int = 600
    max_hourly_replies: int = 20
    max_daily_replies: int = 100
    max_replies_per_user_per_hour: int = 5
    max_replies_per_oid_per_hour: int = 10
    source_circuit_breaker_failures: int = 3


class CookieConfig(BaseModel):
    cookies_file: str = "config/bilibili-cookies.txt"
    refresh_enabled: bool = True
    refresh_token_env: str = "BILIBILI_REFRESH_TOKEN"
    check_interval_minutes: int = 30
    healthcheck_endpoint: str = "https://api.bilibili.com/x/web-interface/nav"


class SafetyConfig(BaseModel):
    sensitive_words: list[str] = Field(default_factory=lambda: [
        "共产党", "法轮功", "台独", "疆独", "藏独", "反华", "颠覆",
        "赌博", "博彩", "色情", "淫秽", "嫖娼", "卖淫", "毒品", "吸毒",
        "诈骗", "传销", "非法集资", "洗钱", "黑客", "木马", "病毒",
        "微信", "QQ", "qq", "加群", "加薇", "加V", "加v",
        "裸聊", "约炮", "包养", "代孕", "人体器官", "枪支", "弹药",
        "爆炸物", "恐怖袭击", "自杀", "自残", "邪教", "迷信",
    ])
    max_length: int = 500
    max_url_count: int = 3
    block_pii: bool = True


class BotConfig(BaseSettings):
    bot: BotSettings = Field(default_factory=BotSettings)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    reply: ReplyConfig = Field(default_factory=ReplyConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    cookie: CookieConfig = Field(default_factory=CookieConfig)
    content_safety: SafetyConfig = Field(default_factory=SafetyConfig)

    @classmethod
    def from_toml(cls, path: str | Path) -> BotConfig:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        with path.open("rb") as f:
            data = tomllib.load(f)

        return cls.model_validate(data)


def load_config(path: str | None = None) -> BotConfig:
    if path is None:
        path = "config/bot-config.toml"
    return BotConfig.from_toml(path)
