from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    source_type: str
    event_key: str
    created_at: int
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def author_id(self) -> str:
        return ""

    @property
    def content(self) -> str:
        return ""

    @property
    def target_id(self) -> str:
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "event_key": self.event_key,
            "created_at": self.created_at,
            "raw_payload": self.raw_payload,
        }


BUSINESS_TYPE_MAP = {1: "video", 11: "dynamic_draw", 17: "dynamic"}


@dataclass
class CommentEvent(Event):
    business_type: str = ""
    oid: str = ""
    rpid: str = ""
    root_rpid: str = ""
    parent_rpid: str = ""
    author_mid: str = ""
    author_name: str = ""
    content_text: str = ""
    at_me: bool = False
    video_title: str = ""
    parent_content: str = ""

    @property
    def author_id(self) -> str:
        return self.author_mid

    @property
    def content(self) -> str:
        return self.content_text

    @property
    def target_id(self) -> str:
        return self.oid

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update({
            "business_type": self.business_type,
            "oid": self.oid,
            "rpid": self.rpid,
            "root_rpid": self.root_rpid,
            "parent_rpid": self.parent_rpid,
            "author_mid": self.author_mid,
            "author_name": self.author_name,
            "content_text": self.content_text,
            "at_me": self.at_me,
            "video_title": self.video_title,
            "parent_content": self.parent_content,
        })
        return data


@dataclass
class DMEvent(Event):
    talker_id: int = 0
    talker_name: str = ""
    dm_content: str = ""
    msg_type: int = 1
    msg_key: int = 0
    recent_messages: list = field(default_factory=list)

    @property
    def author_id(self) -> str:
        return str(self.talker_id)

    @property
    def content(self) -> str:
        return self.dm_content

    @property
    def target_id(self) -> str:
        return str(self.talker_id)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update({
            "talker_id": self.talker_id,
            "talker_name": self.talker_name,
            "content": self.dm_content,
            "msg_type": self.msg_type,
            "msg_key": self.msg_key,
            "recent_count": len(self.recent_messages),
        })
        return data
