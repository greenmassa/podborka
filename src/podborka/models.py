"""Общая модель данных для всех соцсетей."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    X = "x"
    TELEGRAM = "telegram"
    TIKTOK = "tiktok"


class ContentType(StrEnum):
    POST = "post"
    VIDEO = "video"


class AuthorSize(StrEnum):
    """Размер автора по числу подписчиков."""

    MICRO = "micro"  # до 10 тыс
    MID = "mid"  # 10–100 тыс
    MACRO = "macro"  # 100 тыс – 1 млн
    MEGA = "mega"  # от 1 млн


@dataclass
class Query:
    """Отслеживаемый товар или тема.

    Пост попадает в выдачу, если содержит любое ключевое слово, хэштег
    или упоминание аккаунта (@account).
    """

    name: str
    keywords: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    platforms: list[Platform] = field(default_factory=lambda: list(Platform))
    category: str | None = None
    active: bool = True
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class Item:
    """Пост или видео в соцсети."""

    platform: Platform
    external_id: str
    content_type: ContentType
    url: str
    author: str
    text: str
    published_at: datetime
    id: int | None = None
    first_seen_at: datetime | None = None


@dataclass
class Snapshot:
    """Значения счётчиков поста в момент сбора. None — сеть не отдаёт метрику."""

    captured_at: datetime
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    author_followers: int | None = None
    item_id: int | None = None
    id: int | None = None

    @property
    def reactions(self) -> int:
        return (self.likes or 0) + (self.comments or 0) + (self.shares or 0)


@dataclass
class Collected:
    """То, что возвращает сборщик: пост и его текущие счётчики."""

    item: Item
    snapshot: Snapshot
