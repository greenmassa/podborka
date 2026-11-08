from datetime import UTC, datetime, timedelta

import pytest

from podborka.db import Database, ItemFilter
from podborka.models import AuthorSize, Collected, ContentType, Item, Platform, Query, Snapshot
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
    assert len(db.list_items(ItemFilter(query_id=q2.id))) == 1


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

    ranked = db.ranked_items(ItemFilter(query_id=q.id), sort="velocity")
    assert [r.item.external_id for r in ranked] == ["fast", "slow"]
    assert ranked[0].score.velocity == pytest.approx(100)  # 200 / 2 ч
    assert ranked[1].score.velocity == pytest.approx(10)  # +10 за 1 ч
    assert len(ranked[1].snapshots) == 2

    by_eng = db.ranked_items(ItemFilter(query_id=q.id), sort="engagement")
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
    assert len(db.list_items(ItemFilter(platforms=[Platform.X]))) == 1
    assert len(db.list_items(ItemFilter(since=NOW - timedelta(days=1)))) == 1
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


def _add(db, q, ext, hours_ago=1, platform=Platform.YOUTUBE, text=None, seen=NOW, **snap):
    item = make_item(ext, hours_ago, platform)
    if text:
        item.text = text
    item = db.upsert_item(item, q.id, seen)
    db.add_snapshot(item.id, Snapshot(NOW, **snap))
    return item


def ids(ranked):
    return sorted(r.item.external_id for r in ranked)


def test_category_saved_and_filtered(db):
    beauty = db.save_query(Query(name="крем", category="Косметика"))
    toys = db.save_query(Query(name="игрушка", category="Игрушки"))
    db.save_query(Query(name="без категории", category="  "))
    assert db.list_categories() == ["Игрушки", "Косметика"]
    assert db.get_query(beauty.id).category == "Косметика"
    _add(db, beauty, "a", likes=1)
    _add(db, toys, "b", likes=1)
    assert ids(db.ranked_items(ItemFilter(category="Косметика"))) == ["a"]


def test_multi_platform_filter(db):
    q = db.save_query(Query(name="q"))
    for ext, pl in [("y", Platform.YOUTUBE), ("t", Platform.TIKTOK), ("x", Platform.X)]:
        _add(db, q, ext, platform=pl, likes=1)
    f = ItemFilter(platforms=[Platform.TIKTOK, Platform.X])
    assert ids(db.ranked_items(f)) == ["t", "x"]


def test_text_search_is_case_insensitive_for_cyrillic(db):
    q = db.save_query(Query(name="q"))
    _add(db, q, "a", text="Где КУПИТЬ дешевле?", likes=1)
    _add(db, q, "b", text="Распаковка", likes=1)
    assert ids(db.ranked_items(ItemFilter(text="купить"))) == ["a"]
    assert ids(db.ranked_items(ItemFilter(text="  "))) == ["a", "b"]


def test_author_size_and_thresholds(db):
    q = db.save_query(Query(name="q"))
    _add(db, q, "micro", likes=5, views=100, author_followers=900)
    _add(db, q, "mid", likes=50, views=5000, author_followers=40_000)
    _add(db, q, "mega", likes=5000, views=900_000, author_followers=3_000_000)
    _add(db, q, "unknown", likes=50)
    assert ids(db.ranked_items(ItemFilter(author_sizes=[AuthorSize.MICRO, AuthorSize.MEGA]))) == [
        "mega",
        "micro",
    ]
    assert ids(db.ranked_items(ItemFilter(min_reactions=50))) == ["mega", "mid", "unknown"]
    assert ids(db.ranked_items(ItemFilter(min_views=1000))) == ["mega", "mid"]
    # возраст 1 ч → скорость = реакции
    assert ids(db.ranked_items(ItemFilter(min_velocity=100))) == ["mega"]


def test_first_seen_filter(db):
    q = db.save_query(Query(name="q"))
    _add(db, q, "old", seen=NOW - timedelta(days=3), likes=1)
    _add(db, q, "new", seen=NOW - timedelta(hours=2), likes=1)
    assert ids(db.ranked_items(ItemFilter(first_seen_since=NOW - timedelta(hours=6)))) == ["new"]


def test_migration_adds_category(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE queries (id INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " keywords TEXT NOT NULL DEFAULT '[]', hashtags TEXT NOT NULL DEFAULT '[]',"
        " accounts TEXT NOT NULL DEFAULT '[]', platforms TEXT NOT NULL DEFAULT '[]',"
        " active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO queries (name, created_at) VALUES ('старый', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.list_queries()[0].category is None
    db.close()
