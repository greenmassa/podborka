"""Наполнение базы демо-данными: несколько товаров и история сборов за двое суток."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from podborka.collectors.demo import demo_collectors
from podborka.db import Database
from podborka.models import Platform, Query
from podborka.service import run_query

DEMO_QUERIES = [
    Query(name="Stanley Quencher", keywords=["stanley quencher", "стэнли"],
          hashtags=["#stanleycup"]),
    Query(name="Labubu", keywords=["labubu", "лабубу"], hashtags=["#labubu"]),
    Query(name="Dyson Airwrap", keywords=["dyson airwrap"], hashtags=["#airwrap"],
          platforms=[Platform.YOUTUBE, Platform.INSTAGRAM, Platform.TIKTOK]),
    Query(name="Корейский солнцезащитный крем", keywords=["korean sunscreen", "корейский спф"],
          platforms=[Platform.INSTAGRAM, Platform.TELEGRAM, Platform.TIKTOK]),
]  # fmt: skip


def seed(db: Database, now: datetime | None = None, runs: int = 9, step_hours: int = 6) -> None:
    now = now or datetime.now(UTC)
    start = now - timedelta(hours=step_hours * (runs - 1))
    queries = [db.save_query(Query(**{**q.__dict__})) for q in DEMO_QUERIES]
    clock_value = [start]
    collectors = demo_collectors(anchor=start, clock=lambda: clock_value[0])
    for k in range(runs):
        clock_value[0] = start + timedelta(hours=step_hours * k)
        for q in queries:
            run_query(db, q, collectors, now=clock_value[0])
