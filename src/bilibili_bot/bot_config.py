#!/usr/bin/env python3
"""评论机器人配置与现有资产复用。"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any


# 自动检测项目根目录（src/bilibili_bot/ 的上两级）
_BOT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BOT_DIR.parent.parent

# 可通过环境变量覆盖
REPO_ROOT = Path(os.environ.get("BILIBILI_BOT_ROOT", str(_PROJECT_ROOT)))
SCRIPT_ROOT = _BOT_DIR
DATA_ROOT = REPO_ROOT / "data"
CONFIG_ROOT = REPO_ROOT / "config"
WBI_SCRIPT = _BOT_DIR / "bilibili_wbi.py"
DEFAULT_CONFIG_PATH = CONFIG_ROOT / "bot-config.toml"


class ConfigError(RuntimeError):
    """配置错误。"""


@lru_cache(maxsize=1)
def load_wbi_module():
    spec = importlib.util.spec_from_file_location("bilibili_wbi_shared", WBI_SCRIPT)
    if spec is None or spec.loader is None:
        raise ConfigError(f"无法加载 WBI 模块: {WBI_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_cookies_file(filepath: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    return cookies


def write_cookies_file(filepath: str, cookies: dict[str, str]) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Netscape HTTP Cookie File",
        "# Runtime cookies managed by bilibili comment bot",
        "",
    ]
    for key, value in cookies.items():
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}***{value[-keep:]}"


def sign_wbi(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, Any]:
    return load_wbi_module().enc_wbi(params, img_key, sub_key)


def get_user_agents() -> list[str]:
    module = load_wbi_module()
    return list(getattr(module, "USER_AGENTS", [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    ]))


def random_user_agent() -> str:
    return random.choice(get_user_agents())


def build_cookie_header(cookies: dict[str, str]) -> str:
    items = []
    for key, value in cookies.items():
        if value:
            items.append(f"{key}={value}")
    return "; ".join(items)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def default_config() -> dict[str, Any]:
    return {
        "bot": {
            "enabled": True,
            "poll_interval_seconds": 30,
            "run_mode": "daemon",
            "conservative_mode": True,
            "log_level": "INFO",
            "request_timeout_seconds": 25,
            "source_failure_cooldown_seconds": 180,
        },
        "sources": {
            "msgfeed": {
                "enabled": True,
                "poll_interval_seconds": 20,
                "page_size": 10,
            },
            "mention": {
                "enabled": True,
                "page_size": 10,
            },
            "own_video": {
                "enabled": True,
                "video_page_size": 5,
                "comment_page_size": 10,
                "max_retries": 2,
                "retry_sleep_seconds": 6,
            },
            "own_dynamic": {
                "enabled": True,
                "dynamic_page_size": 5,
                "comment_page_size": 10,
            },
            "dm": {
                "enabled": False,
                "poll_interval_seconds": 60,
                "max_reply_per_round": 5,
                "skip_keywords": ["广告", "推广", "加微信"],
                "whitelist_mids": [],
            },
        },
        "filters": {
            "skip_self": True,
            "skip_empty": True,
            "skip_pure_emoji": True,
            "min_meaningful_length": 2,
            "blacklist_mids": [],
            "duplicate_window_minutes": 1440,
        },
        "ai": {
            "primary_provider": "deepseek",
            "fallback_provider": "opencode-local",
            "timeout_seconds": 25,
            "max_reply_chars": 100,
            "providers": {
                "deepseek": {
                    "type": "openai_compatible",
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "api_key_env": "DEEPSEEK_API_KEY",
                },
                "opencode-local": {
                    "type": "opencode_local",
                    "command": "opencode",
                    "dir": str(_PROJECT_ROOT),
                },
            },
        },
        "reply": {
            "system_prompt": "你是一个友善、自然、不过度营销的B站UP主。请根据评论内容生成简短自然的中文回复，避免机械、避免重复，不超过100字。",
            "temperature": 0.7,
            "max_tokens": 200,
            "prefix": "",
            "mention_style": "friendly",
        },
        "dm_reply": {
            "system_prompt": "你是一个友善的B站用户。请根据私信内容生成简短自然的中文回复，避免机械、避免重复，不超过100字。",
            "temperature": 0.7,
            "max_tokens": 200,
        },
        "rate_limit": {
            "min_request_interval_seconds": 3,
            "reply_delay_min_seconds": 8,
            "reply_delay_max_seconds": 20,
            "max_retries": 3,
            "backoff_base_seconds": 10,
            "circuit_breaker_failures": 5,
            "circuit_breaker_cooldown_seconds": 600,
            "max_hourly_replies": 20,
            "max_daily_replies": 100,
            "source_circuit_breaker_failures": 3,
        },
        "cookie": {
            "cookies_file": str(CONFIG_ROOT / "bilibili-cookies.txt"),
            "refresh_enabled": True,
            "refresh_token_env": "BILIBILI_REFRESH_TOKEN",
            "check_interval_minutes": 30,
            "healthcheck_endpoint": "https://api.bilibili.com/x/web-interface/nav",
        },
    }


def load_config(config_path: str | None = None) -> dict[str, Any]:
    path = Path(config_path or DEFAULT_CONFIG_PATH)
    data = default_config()
    if path.exists():
        with open(path, "rb") as f:
            parsed = tomllib.load(f)
        data = _deep_merge(data, parsed)
    validate_config(data)
    data["_meta"] = {
        "config_path": str(path),
        "repo_root": str(REPO_ROOT),
        "data_root": str(DATA_ROOT),
        "script_root": str(SCRIPT_ROOT),
    }
    return data


def validate_config(config: dict[str, Any]) -> None:
    cookies_file = Path(config["cookie"]["cookies_file"])
    if not cookies_file.exists():
        raise ConfigError(f"Cookies 文件不存在: {cookies_file}")

    providers = config["ai"].get("providers", {})
    primary_name = config["ai"].get("primary_provider")
    fallback_name = config["ai"].get("fallback_provider")
    if primary_name not in providers:
        raise ConfigError(f"未定义主 Provider: {primary_name}")
    if fallback_name not in providers:
        raise ConfigError(f"未定义 fallback Provider: {fallback_name}")

    primary = providers[primary_name]
    if primary.get("type") == "openai_compatible" and not primary.get("api_key_env"):
        raise ConfigError(f"主 Provider {primary_name} 缺少 api_key_env")

    fallback = providers[fallback_name]
    if fallback.get("type") == "opencode_local":
        command = fallback.get("command", "opencode")
        if not shutil_which(command):
            raise ConfigError(f"未找到 fallback 命令: {command}")


def shutil_which(command: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / command
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
