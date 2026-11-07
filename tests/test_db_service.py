from datetime import UTC, datetime, timedelta

import pytest

from podborka.db import Database
from podborka.models import Collected, ContentType, Item, Platform, Query, Snapshot
from podborka.service import query_trend, run_query

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def make_item(ext: str, hours_ago: float, platform=Platform.YOUTUBE) -> Item:
    return Item(
        platform=platform,
        external_id=ext,
        content_type=ContentType.VIDEO,
        url=f"https://example.com/{ext}",
        author="author",
        text=f"обзор {ext}",
        published_at=NOW - timedelta(hours=hours_ago),
    )


class FakeCollector:
    def __init__(
        self, platform: Platform, batches: list[list[Collected]] | None = None, fail=False
    ):  # noqa: E501
        self.platform = platform
        self.batches = batches or []
        self.fail = fail

    def collect(self, query: Query):
        if self.fail:
            raise RuntimeError("API недоступно")
        return self.batches.pop(0) if self.batches else []


@pytest.fixture
def db():
    d = Database()
    yield d
    d.close()


def test_query_roundtrip(db):
    q = db.save_query(
        Query(
            name="Стэнли",
            keywords=["stanley cup"],
            hashtags=["#stanley"],
            platforms=[Platform.TIKTOK, Platform.X],
        )
    )
    got = db.get_query(q.id)
    assert got.name == "Стэнли"
    assert got.hashtags == ["#stanley"]
    assert got.platforms == [Platform.TIKTOK, Platform.X]

    got.active = False
    db.save_query(got)
    assert db.list_queries(active_only=True) == []


def test_item_shared_between_queries_stored_once(db):
    q1 = db.save_query(Query(name="a"))
    q2 = db.save_query(Query(name="b"))
    db.upsert_item(make_item("v1", 1), q1.id, NOW)
    db.upsert_item(make_item("v1", 1), q2.id, NOW)
    assert len(db.list_items()) == 1
    assert len(db.list_items(query_id=q2.id)) == 1


def test_naive_datetime_rejected(db):
    q = db.save_query(Query(name="a"))
    item = make_item("v1", 1)
    item.published_at = item.published_at.replace(tzinfo=None)
    with pytest.raises(ValueError):
        db.upsert_item(item, q.id, NOW)


def test_run_query_writes_snapshots_and_ranks(db):
    q = db.save_query(Query(name="товар", platforms=[Platform.YOUTUBE]))
    yt = FakeCollector(
        Platform.YOUTUBE,
        [
            [
                Collected(make_item("slow", 10), Snapshot(NOW, views=1000, likes=50)),
                Collected(make_item("fast", 2), Snapshot(NOW, views=1000, likes=200)),
            ],
            [
                Collected(
                    make_item("slow", 10),
                    Snapshot(NOW + timedelta(hours=1), views=1200, likes=60),
                ),
            ],
        ],
    )
    run_query(db, q, [yt], now=NOW)
    run_query(db, q, [yt], now=NOW + timedelta(hours=1))

    ranked = db.ranked_items(query_id=q.id, sort="velocity")
    assert [r.item.external_id for r in ranked] == ["fast", "slow"]
    assert ranked[0].score.velocity == pytest.approx(100)  # 200 / 2 ч
    assert ranked[1].score.velocity == pytest.approx(10)  # +10 за 1 ч
    assert len(ranked[1].snapshots) == 2

    by_eng = db.ranked_items(query_id=q.id, sort="engagement")
    assert by_eng[0].item.external_id == "fast"


def test_run_query_skips_other_platforms_and_survives_errors(db):
    q = db.save_query(Query(name="товар", platforms=[Platform.X, Platform.TIKTOK]))
    yt = FakeCollector(Platform.YOUTUBE, [[Collected(make_item("v", 1), Snapshot(NOW, likes=1))]])
    x = FakeCollector(Platform.X, fail=True)
    tt = FakeCollector(
        Platform.TIKTOK,
        [[Collected(make_item("t", 1, Platform.TIKTOK), Snapshot(NOW, views=10, likes=1))]],
    )
    res = run_query(db, q, [yt, x, tt], now=NOW)
    assert res.items == 1
    assert set(res.errors) == {Platform.X}
    assert [i.platform for i in db.list_items()] == [Platform.TIKTOK]


def test_filters(db):
    q = db.save_query(Query(name="товар"))
    for ext, hrs, pl in [("a", 1, Platform.YOUTUBE), ("b", 50, Platform.X)]:
        item = db.upsert_item(make_item(ext, hrs, pl), q.id, NOW)
        db.add_snapshot(item.id, Snapshot(NOW, likes=1))
    assert len(db.list_items(platform=Platform.X)) == 1
    assert len(db.list_items(since=NOW - timedelta(days=1))) == 1
    with pytest.raises(ValueError):
        db.ranked_items(sort="nope")


def test_query_trend(db):
    q = db.save_query(Query(name="товар"))
    for ext, hrs, pl in [
        ("a", 1, Platform.YOUTUBE),
        ("b", 3, Platform.TIKTOK),
        ("c", 5, Platform.TIKTOK),
        ("d", 30, Platform.X),
    ]:
        item = db.upsert_item(make_item(ext, hrs, pl), q.id, NOW)
        db.add_snapshot(item.id, Snapshot(NOW, likes=10))
    t = query_trend(db, q, now=NOW)
    assert (t.mentions_total, t.mentions_recent, t.mentions_previous) == (4, 3, 1)
    assert t.growth == 3.0
    assert t.reactions_total == 40
    assert t.by_platform == {Platform.YOUTUBE: 1, Platform.TIKTOK: 2, Platform.X: 1}
