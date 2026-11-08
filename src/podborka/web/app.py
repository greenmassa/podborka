"""Веб-интерфейс: сводка по товарам, лента постов, карточка поста, запросы."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from podborka.collectors.base import Collector
from podborka.db import SORT_KEYS, Database, ItemFilter
from podborka.metrics import score
from podborka.models import AuthorSize, ContentType, Platform, Query
from podborka.service import query_trend, run_query
from podborka.web import fmt

HERE = Path(__file__).parent

PERIODS = {
    "24h": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
PERIOD_LABELS = {"24h": "24 часа", "7d": "7 дней", "30d": "30 дней", "all": "Всё время"}
SEEN = {"6h": timedelta(hours=6), "24h": timedelta(days=1), "7d": timedelta(days=7)}
SEEN_LABELS = {"": "Когда угодно", "6h": "За 6 часов", "24h": "За 24 часа", "7d": "За 7 дней"}
SORT_LABELS = {
    "velocity": "Скорость",
    "engagement": "Вовлечённость",
    "reactions": "Реакции",
    "published": "Новые",
}
assert SORT_LABELS.keys() == SORT_KEYS.keys()
AUTHOR_LABELS = {
    AuthorSize.MICRO: "Микро · до 10 тыс",
    AuthorSize.MID: "Средние · 10–100 тыс",
    AuthorSize.MACRO: "Крупные · 100 тыс – 1 млн",
    AuthorSize.MEGA: "Мега · от 1 млн",
}
# Пороги в выпадающих списках: значение → подпись
MIN_REACTIONS = {0: "Любые", 10: "от 10", 100: "от 100", 1000: "от 1 тыс", 10000: "от 10 тыс"}
MIN_VIEWS = {0: "Любые", 1000: "от 1 тыс", 10000: "от 10 тыс", 100000: "от 100 тыс"}
MIN_VELOCITY = {0: "Любая", 1: "от 1/ч", 10: "от 10/ч", 50: "от 50/ч", 200: "от 200/ч"}
# «Растущий» товар: упоминаний за сутки в 1,5 раза больше, чем за прошлые
GROWING_RATIO = 1.5

# Фильтры, спрятанные под «Ещё фильтры», — чтобы показать, сколько из них включено
EXTRA_FILTERS = ("content_type", "author", "min_reactions", "min_views", "min_velocity", "seen")


def split_list(raw: str) -> list[str]:
    """«a, b\\nc» → ['a', 'b', 'c']"""
    parts = (p.strip() for line in raw.splitlines() for p in line.split(","))
    return [p for p in parts if p]


def _int(value: str | None, allowed: dict[int, str]) -> int:
    try:
        v = int(value or 0)
    except ValueError:
        return 0
    return v if v in allowed else 0


def parse_feed_params(params, now: datetime) -> tuple[ItemFilter, str, dict]:
    """Query-string → (фильтр, сортировка, значения для формы). Мусор игнорируется."""
    query_id = params.get("query_id", "")
    category = params.get("category", "").strip()
    platforms = [p for p in params.getlist("platform") if p in Platform]
    content_type = params.get("content_type", "")
    content_type = content_type if content_type in ContentType else ""
    authors = [a for a in params.getlist("author") if a in AuthorSize]
    period = params.get("period", "7d")
    period = period if period in PERIODS else "7d"
    seen = params.get("seen", "")
    seen = seen if seen in SEEN else ""
    sort = params.get("sort", "velocity")
    sort = sort if sort in SORT_KEYS else "velocity"
    text = params.get("q", "").strip()[:100]
    form = {
        "query_id": query_id if query_id.isdigit() else "",
        "category": category,
        "platform": platforms,
        "content_type": content_type,
        "author": authors,
        "period": period,
        "seen": seen,
        "sort": sort,
        "q": text,
        "min_reactions": _int(params.get("min_reactions"), MIN_REACTIONS),
        "min_views": _int(params.get("min_views"), MIN_VIEWS),
        "min_velocity": _int(params.get("min_velocity"), MIN_VELOCITY),
    }
    f = ItemFilter(
        query_id=int(query_id) if query_id.isdigit() else None,
        category=category or None,
        platforms=[Platform(p) for p in platforms],
        content_type=ContentType(content_type) if content_type else None,
        since=now - PERIODS[period] if PERIODS[period] else None,
        first_seen_since=now - SEEN[seen] if seen else None,
        text=text or None,
        author_sizes=[AuthorSize(a) for a in authors],
        min_reactions=form["min_reactions"],
        min_views=form["min_views"],
        min_velocity=form["min_velocity"],
    )
    return f, sort, form


def create_app(db: Database, collectors: Sequence[Collector]) -> FastAPI:
    app = FastAPI(title="Podborka", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    fmt.register(templates.env)
    templates.env.globals.update(
        Platform=Platform,
        ContentType=ContentType,
        PERIOD_LABELS=PERIOD_LABELS,
        SEEN_LABELS=SEEN_LABELS,
        SORT_LABELS=SORT_LABELS,
        AUTHOR_LABELS=AUTHOR_LABELS,
        MIN_REACTIONS=MIN_REACTIONS,
        MIN_VIEWS=MIN_VIEWS,
        MIN_VELOCITY=MIN_VELOCITY,
    )

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"now": datetime.now(UTC), **ctx})

    def get_query_or_404(query_id: int) -> Query:
        q = db.get_query(query_id)
        if q is None:
            raise HTTPException(404, "Запрос не найден")
        return q

    # --- сводка ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, category: str = "", growing: str = ""):
        now = datetime.now(UTC)
        category = category.strip()
        queries = [q for q in db.list_queries() if not category or q.category == category]
        trends = [query_trend(db, q, now=now) for q in queries]
        if growing:
            trends = [
                t
                for t in trends
                if (t.growth is None and t.mentions_recent > 0)
                or (t.growth is not None and t.growth >= GROWING_RATIO)
            ]
        trends.sort(key=lambda t: (t.mentions_recent, t.growth or 0), reverse=True)
        top = db.ranked_items(
            ItemFilter(since=now - timedelta(days=7), category=category or None),
            sort="velocity",
            limit=8,
        )
        return page(
            request,
            "index.html",
            trends=trends,
            top=top,
            categories=db.list_categories(),
            f={"category": category, "growing": bool(growing)},
        )

    # --- лента -------------------------------------------------------------

    @app.get("/items", response_class=HTMLResponse)
    def items(request: Request):
        f, sort, form = parse_feed_params(request.query_params, datetime.now(UTC))
        ranked = db.ranked_items(f, sort=sort, limit=200)
        ctx = {
            "ranked": ranked,
            "queries": db.list_queries(),
            "categories": db.list_categories(),
            "f": form,
            "extra_active": sum(1 for k in EXTRA_FILTERS if form[k]),
        }
        # htmx меняет только ленту при смене фильтра
        if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "feed":
            return page(request, "_feed.html", **ctx)
        return page(request, "items.html", **ctx)

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_detail(request: Request, item_id: int):
        item = db.get_item(item_id)
        if item is None:
            raise HTTPException(404, "Пост не найден")
        snaps = db.get_snapshots(item_id)
        chart = {
            "labels": [s.captured_at.astimezone(UTC).strftime("%d.%m %H:%M") for s in snaps],
            "reactions": [s.reactions for s in snaps],
            "views": [s.views for s in snaps],
        }
        return page(
            request,
            "item.html",
            item=item,
            snaps=snaps,
            sc=score(snaps, item.published_at),
            chart=chart,
        )

    # --- запросы -----------------------------------------------------------

    @app.get("/queries", response_class=HTMLResponse)
    def queries(request: Request):
        return page(
            request,
            "queries.html",
            queries=db.list_queries(),
            categories=db.list_categories(),
            edit=None,
        )

    @app.get("/queries/{query_id}/edit", response_class=HTMLResponse)
    def query_edit(request: Request, query_id: int):
        return page(
            request,
            "queries.html",
            queries=db.list_queries(),
            categories=db.list_categories(),
            edit=get_query_or_404(query_id),
        )

    @app.post("/queries")
    def query_save(
        name: Annotated[str, Form()],
        keywords: Annotated[str, Form()] = "",
        hashtags: Annotated[str, Form()] = "",
        accounts: Annotated[str, Form()] = "",
        category: Annotated[str, Form()] = "",
        platforms: Annotated[list[str] | None, Form()] = None,
        active: Annotated[str | None, Form()] = None,
        query_id: Annotated[int | None, Form()] = None,
    ):
        name = name.strip()
        if not name:
            raise HTTPException(422, "Нужно название")
        q = get_query_or_404(query_id) if query_id else Query(name=name)
        q.name = name
        q.keywords = split_list(keywords)
        q.hashtags = [h if h.startswith("#") else f"#{h}" for h in split_list(hashtags)]
        q.accounts = [a if a.startswith("@") else f"@{a}" for a in split_list(accounts)]
        q.platforms = [Platform(p) for p in (platforms or []) if p in Platform]
        q.category = category.strip()[:60] or None
        q.active = active is not None if query_id else True
        db.save_query(q)
        return RedirectResponse("/queries", status_code=303)

    @app.post("/queries/{query_id}/delete")
    def query_delete(query_id: int):
        db.delete_query(query_id)
        return RedirectResponse("/queries", status_code=303)

    @app.post("/queries/{query_id}/run", response_class=HTMLResponse)
    def query_run(request: Request, query_id: int):
        res = run_query(db, get_query_or_404(query_id), collectors)
        return page(request, "_run_result.html", res=res, query_id=query_id)

    return app
