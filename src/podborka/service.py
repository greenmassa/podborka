"""Сбор по запросам и сводка по товару."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from podborka.collectors.base import Collector
from podborka.db import Database, ItemFilter
from podborka.metrics import mention_growth
from podborka.models import Platform, Query

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    items: int = 0
    errors: dict[Platform, str] = field(default_factory=dict)


def run_query(
    db: Database, query: Query, collectors: Iterable[Collector], now: datetime | None = None
) -> RunResult:
    """Прогоняет запрос через сборщики его соцсетей и пишет посты и снимки.

    Ошибка одной сети не останавливает остальные.
    """
    now = now or datetime.now(UTC)
    result = RunResult()
    for collector in collectors:
        if collector.platform not in query.platforms:
            continue
        try:
            for c in collector.collect(query):
                item = db.upsert_item(c.item, query.id, seen_at=now)
                db.add_snapshot(item.id, c.snapshot)
                result.items += 1
        except Exception as e:  # noqa: BLE001 — сеть/API могут упасть как угодно
            log.exception("сборщик %s упал на запросе %s", collector.platform, query.name)
            result.errors[collector.platform] = str(e)
    return result


@dataclass
class QueryTrend:
    """Сводка по товару: сколько упоминаний и растёт ли их число."""

    query: Query
    mentions_total: int
    mentions_recent: int
    mentions_previous: int
    growth: float | None
    reactions_total: int
    by_platform: dict[Platform, int]


def query_trend(
    db: Database, query: Query, now: datetime | None = None, window: timedelta = timedelta(days=1)
) -> QueryTrend:
    now = now or datetime.now(UTC)
    ranked = db.ranked_items(ItemFilter(query_id=query.id), limit=10**9)
    recent, previous, growth = mention_growth(
        (r.item.published_at for r in ranked), now=now, window=window
    )
    by_platform: dict[Platform, int] = {}
    for r in ranked:
        by_platform[r.item.platform] = by_platform.get(r.item.platform, 0) + 1
    return QueryTrend(
        query=query,
        mentions_total=len(ranked),
        mentions_recent=recent,
        mentions_previous=previous,
        growth=growth,
        reactions_total=sum(r.score.reactions for r in ranked),
        by_platform=by_platform,
    )
