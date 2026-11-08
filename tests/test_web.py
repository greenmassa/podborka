from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from podborka.collectors.demo import DemoCollector, demo_collectors
from podborka.db import Database, ItemFilter
from podborka.demo import seed
from podborka.models import Platform, Query
from podborka.web.app import create_app, split_list
from podborka.web.fmt import ago, num, pct


@pytest.fixture
def db():
    d = Database()
    seed(d, now=datetime.now(UTC), runs=3)
    yield d
    d.close()


@pytest.fixture
def client(db):
    return TestClient(create_app(db, demo_collectors()))


def test_index_shows_products(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Labubu" in r.text
    assert "Быстрее всего набирают реакции" in r.text


def test_feed_filters(client, db):
    q = db.list_queries()[0]
    r = client.get(f"/items?query_id={q.id}&platform=tiktok&period=all&sort=engagement")
    assert r.status_code == 200
    assert 'class="badge pl-tiktok"' in r.text
    assert 'class="badge pl-youtube"' not in r.text


def test_feed_new_filters(client, db):
    r = client.get(
        "/items?category=Игрушки&platform=tiktok&platform=instagram&author=micro&author=mid"
        "&min_reactions=10&min_views=0&min_velocity=1&seen=7d&q=labubu&period=all"
    )
    assert r.status_code == 200
    assert "Labubu" in r.text
    assert "Stanley" not in r.text.split('id="feed"')[1]
    assert 'class="badge pl-youtube"' not in r.text
    # включённые «ещё фильтры» раскрывают блок и показывают счётчик
    assert '<details class="more" open>' in r.text
    assert 'class="count">4<' in r.text


def test_index_category_and_growing(client):
    r = client.get("/?category=Игрушки")
    assert "Labubu" in r.text and "Dyson Airwrap</span>" not in r.text
    r = client.get("/?growing=1")
    assert r.status_code == 200
    assert "Только растущие" in r.text


def test_feed_htmx_returns_fragment(client):
    r = client.get("/items", headers={"HX-Request": "true", "HX-Target": "feed"})
    assert r.status_code == 200
    assert "<html" not in r.text


def test_feed_ignores_garbage_params(client):
    r = client.get(
        "/items?platform=myspace&sort=drop&period=1y&query_id=x&author=giant"
        "&min_reactions=abc&min_views=7&seen=forever"
    )
    assert r.status_code == 200


def test_item_detail(client, db):
    item = db.list_items()[0]
    r = client.get(f"/items/{item.id}")
    assert r.status_code == 200
    assert "Рост реакций" in r.text
    assert client.get("/items/999999").status_code == 404


def test_query_crud_and_run(client, db):
    r = client.post(
        "/queries",
        data={
            "name": "Тестовый товар",
            "keywords": "foo, bar\nbaz",
            "hashtags": "tag1, #tag2",
            "accounts": "brand",
            "category": " Косметика ",
            "platforms": ["x", "tiktok"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    q = next(q for q in db.list_queries() if q.name == "Тестовый товар")
    assert q.keywords == ["foo", "bar", "baz"]
    assert q.hashtags == ["#tag1", "#tag2"]
    assert q.accounts == ["@brand"]
    assert q.platforms == [Platform.X, Platform.TIKTOK]
    assert q.category == "Косметика"

    r = client.post(f"/queries/{q.id}/run")
    assert "Собрано" in r.text
    assert {i.platform for i in db.list_items(ItemFilter(query_id=q.id))} <= {
        Platform.X,
        Platform.TIKTOK,
    }

    client.post("/queries", data={"query_id": q.id, "name": "Новое имя", "platforms": ["x"]})
    q2 = db.get_query(q.id)
    assert q2.name == "Новое имя" and not q2.active

    client.post(f"/queries/{q.id}/delete")
    assert db.get_query(q.id) is None


def test_demo_counters_grow(db):
    q = db.save_query(Query(name="рост", platforms=[Platform.TIKTOK]))
    t = [datetime(2026, 1, 1, tzinfo=UTC)]
    c = DemoCollector(Platform.TIKTOK, anchor=t[0], clock=lambda: t[0])
    first = {x.item.external_id: x.snapshot.reactions for x in c.collect(q)}
    t[0] = t[0].replace(day=2)
    second = {x.item.external_id: x.snapshot.reactions for x in c.collect(q)}
    assert first and set(first) <= set(second)
    assert all(second[k] >= first[k] for k in first)


def test_split_list():
    assert split_list(" a, b \n\n c ,") == ["a", "b", "c"]


def test_formatters():
    assert num(12345) == "12,3 тыс"
    assert num(2_000_000) == "2 млн"
    assert num(None) == "—"
    assert num(3.14) == "3,1"
    assert pct(0.0532) == "5,3%"
    now = datetime(2026, 1, 3, tzinfo=UTC)
    assert ago(datetime(2026, 1, 1, tzinfo=UTC), now) == "2 дня назад"
