import pytest
from bilibili_bot.config import BotConfig


def test_default_config():
    config = BotConfig()
    assert config.bot.poll_interval_seconds == 5
    assert config.ai.primary_provider == "deepseek"
    assert config.rate_limit.max_hourly_replies == 20


def test_config_from_toml(tmp_path):
    toml_content = """
[bot]
poll_interval_seconds = 60

[ai]
primary_provider = "test"
"""
    config_file = tmp_path / "test-config.toml"
    config_file.write_text(toml_content)

    config = BotConfig.from_toml(config_file)
    assert config.bot.poll_interval_seconds == 60
    assert config.ai.primary_provider == "test"
