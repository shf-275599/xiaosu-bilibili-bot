#!/usr/bin/env python3
"""AI 输出安全审查模块。"""

from __future__ import annotations

import re
from dataclasses import dataclass


# 敏感词列表（可根据需要扩展）
DEFAULT_SENSITIVE_WORDS = [
    "共产党", "法轮功", "台独", "疆独", "藏独", "反华", "颠覆",
    "赌博", "博彩", "色情", "淫秽", "嫖娼", "卖淫", "毒品", "吸毒",
    "诈骗", "传销", "非法集资", "洗钱", "黑客", "木马", "病毒",
    "微信", "QQ", "qq", "加群", "加薇", "加V", "加v",
    "裸聊", "约炮", "包养", "代孕", "人体器官", "枪支", "弹药",
    "爆炸物", "恐怖袭击", "自杀", "自残", "邪教", "迷信",
]

# 个人信息模式
PII_PATTERNS = [
    (r"\b1[3-9]\d{9}\b", "手机号"),
    (r"\b\d{17}[\dXx]\b", "身份证号"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "邮箱"),
    (r"\b\d{16,19}\b", "银行卡号"),
]

# 风险阈值
MAX_SENSITIVE_WORDS = 0  # 出现即拦截
MAX_PII_COUNT = 1
MAX_URL_COUNT = 3
MAX_LENGTH = 500


@dataclass
class SafetyCheckResult:
    safe: bool
    reason: str = ""
    risk_level: str = "none"  # none, low, medium, high


class ContentSafetyChecker:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.sensitive_words = cfg.get("sensitive_words", DEFAULT_SENSITIVE_WORDS)
        self.max_length = cfg.get("max_length", MAX_LENGTH)
        self.max_url_count = cfg.get("max_url_count", MAX_URL_COUNT)
        self.block_pii = cfg.get("block_pii", True)
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        self.sensitive_pattern = re.compile(
            "|".join(re.escape(w) for w in self.sensitive_words),
            re.IGNORECASE,
        )
        self.url_pattern = re.compile(
            r"https?://[^\s]+|www\.[^\s]+",
            re.IGNORECASE,
        )

    def check(self, text: str) -> SafetyCheckResult:
        if not text or not text.strip():
            return SafetyCheckResult(False, "空内容", "high")

        if len(text) > self.max_length:
            return SafetyCheckResult(
                False,
                f"内容过长（{len(text)} 字，上限 {self.max_length}）",
                "medium",
            )

        sensitive_matches = self.sensitive_pattern.findall(text)
        if sensitive_matches:
            return SafetyCheckResult(
                False,
                f"包含敏感词: {', '.join(set(sensitive_matches[:3]))}",
                "high",
            )

        url_count = len(self.url_pattern.findall(text))
        if url_count > self.max_url_count:
            return SafetyCheckResult(
                False,
                f"包含过多链接（{url_count} 个，上限 {self.max_url_count}）",
                "medium",
            )

        if self.block_pii:
            pii_found = []
            for pattern, pii_type in PII_PATTERNS:
                if re.search(pattern, text):
                    pii_found.append(pii_type)
            if len(pii_found) >= MAX_PII_COUNT:
                return SafetyCheckResult(
                    False,
                    f"包含个人信息: {', '.join(pii_found)}",
                    "high",
                )

        return SafetyCheckResult(True, "内容安全", "none")


def default_safety_checker() -> ContentSafetyChecker:
    return ContentSafetyChecker()
