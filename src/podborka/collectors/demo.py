"""Демо-сборщик: правдоподобные посты со счётчиками, растущими со временем.

Нужен, пока не подключены реальные API. Посты детерминированы (зависят от
запроса и сети), поэтому повторный сбор находит те же посты, но с большими
счётчиками — так видно скорость набора реакций.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

from podborka.models import Collected, ContentType, Item, Platform, Query, Snapshot

TEMPLATES = [
    "Распаковка: {name}",
    "{name} — стоит ли брать? Честный обзор",
    "Нашла {name} в 2 раза дешевле",
    "Месяц с {name}: что понравилось и что нет",
    "Все спрашивают про {name}, отвечаю",
    "{name} vs аналоги — сравнение",
    "Почему {name} везде в ленте?",
    "Купил {name} по вашим советам",
]
AUTHORS = ["anna.reviews", "techdad", "shopaholic_kz", "lifehacks", "mira.home", "gadget_lab",
           "deals_daily", "olzhas.buys", "beauty.notes", "unbox_it"]  # fmt: skip

SEARCH_URL = {
    Platform.YOUTUBE: "https://www.youtube.com/results?search_query={q}",
    Platform.INSTAGRAM: "https://www.instagram.com/explore/search/keyword/?q={q}",
    Platform.X: "https://x.com/search?q={q}",
    Platform.TELEGRAM: "https://t.me/s/telegram?q={q}",
    Platform.TIKTOK: "https://www.tiktok.com/search?q={q}",
}

# Что сеть отдаёт: просмотры есть не везде, подписчики — почти везде.
HAS_VIEWS = {Platform.YOUTUBE, Platform.TIKTOK, Platform.TELEGRAM}
VIDEO = {Platform.YOUTUBE, Platform.TIKTOK}


class DemoCollector:
    def __init__(
        self,
        platform: Platform,
        anchor: datetime | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        posts_per_query: int = 8,
    ):
        self.platform = platform
        # Посты публикуются в окне [anchor - 24 ч, anchor + 48 ч].
        self.anchor = anchor or clock()
        self.clock = clock
        self.posts_per_query = posts_per_query

    def collect(self, query: Query) -> Iterable[Collected]:
        now = self.clock()
        rng = random.Random(f"{query.id}:{query.name}:{self.platform}")
        q = quote_plus(query.keywords[0] if query.keywords else query.name)
        # У каждого товара своя «сила» — чтобы тренды различались.
        hype = rng.uniform(0.3, 3.0)
        count = max(1, round(self.posts_per_query * rng.uniform(0.4, 1.6)))
        for n in range(count):
            published = self.anchor + timedelta(hours=rng.uniform(-24, 48))
            followers = int(rng.lognormvariate(9, 1.5))
            rate = rng.lognormvariate(1, 1.2) * hype  # реакций/ч в начале
            text = rng.choice(TEMPLATES).format(name=query.name)
            author = rng.choice(AUTHORS)
            view_ratio = rng.uniform(12, 40)
            # Все случайные значения берём до фильтра, иначе параметры
            # следующих постов «поедут» между сборами.
            if published > now:
                continue
            hours = max((now - published).total_seconds() / 3600, 0.1)
            reactions = rate * hours**0.8 * (1 + followers / 50_000)
            likes = int(reactions * 0.8)
            comments = int(reactions * 0.12)
            shares = int(reactions * 0.08) if self.platform != Platform.YOUTUBE else None
            views = int(reactions * view_ratio) if self.platform in HAS_VIEWS else None
            yield Collected(
                item=Item(
                    platform=self.platform,
                    external_id=f"demo-{query.id}-{n}",
                    content_type=ContentType.VIDEO if self.platform in VIDEO else ContentType.POST,
                    url=SEARCH_URL[self.platform].format(q=q),
                    author=author,
                    text=text,
                    published_at=published,
                ),
                snapshot=Snapshot(
                    captured_at=now,
                    views=views,
                    likes=likes,
                    comments=comments,
                    shares=shares,
                    author_followers=followers,
                ),
            )


def demo_collectors(**kw) -> list[DemoCollector]:
    return [DemoCollector(p, **kw) for p in Platform]
