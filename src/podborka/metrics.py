"""Метрики популярности.

Вовлечённость — доля реакций (лайки + комментарии + репосты) от просмотров,
а если сеть не отдаёт просмотры — от числа подписчиков автора.

Скорость — прирост реакций в час между двумя последними снимками;
если снимок один — реакции, делённые на часы с момента публикации.

Тренд товара — во сколько раз число новых упоминаний за последнее окно
больше, чем за предыдущее такое же окно.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from podborka.models import AuthorSize, Snapshot

# Не делим на очень малый возраст поста: пост минутной давности с 10 лайками
# не должен получать скорость 600/ч.
MIN_AGE_HOURS = 0.5


AUTHOR_SIZE_LIMITS = [
    (10_000, AuthorSize.MICRO),
    (100_000, AuthorSize.MID),
    (1_000_000, AuthorSize.MACRO),
]


def author_size(followers: int | None) -> AuthorSize | None:
    if followers is None:
        return None
    for limit, size in AUTHOR_SIZE_LIMITS:
        if followers < limit:
            return size
    return AuthorSize.MEGA


def engagement_rate(snapshot: Snapshot) -> float | None:
    if snapshot.views:
        return snapshot.reactions / snapshot.views
    if snapshot.author_followers:
        return snapshot.reactions / snapshot.author_followers
    return None


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def velocity(snapshots: Sequence[Snapshot], published_at: datetime) -> float | None:
    """Реакций в час. Снимки могут быть в любом порядке."""
    if not snapshots:
        return None
    ordered = sorted(snapshots, key=lambda s: s.captured_at)
    last = ordered[-1]
    if len(ordered) >= 2:
        prev = ordered[-2]
        hours = _hours(last.captured_at - prev.captured_at)
        if hours > 0:
            return max(last.reactions - prev.reactions, 0) / hours
    age = max(_hours(last.captured_at - published_at), MIN_AGE_HOURS)
    return last.reactions / age


def mention_growth(
    published: Iterable[datetime], now: datetime, window: timedelta = timedelta(days=1)
) -> tuple[int, int, float | None]:
    """Упоминания за последнее окно, за предыдущее и их отношение.

    Отношение None, если в прошлом окне упоминаний не было.
    """
    recent = previous = 0
    for ts in published:
        if now - window < ts <= now:
            recent += 1
        elif now - 2 * window < ts <= now - window:
            previous += 1
    ratio = recent / previous if previous else None
    return recent, previous, ratio


@dataclass
class ItemScore:
    reactions: int
    views: int | None
    engagement: float | None
    velocity: float | None
    author_size: AuthorSize | None = None


def score(snapshots: Sequence[Snapshot], published_at: datetime) -> ItemScore | None:
    if not snapshots:
        return None
    last = max(snapshots, key=lambda s: s.captured_at)
    return ItemScore(
        reactions=last.reactions,
        views=last.views,
        engagement=engagement_rate(last),
        velocity=velocity(snapshots, published_at),
        author_size=author_size(last.author_followers),
    )
