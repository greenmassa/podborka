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
from podborka.db import SORT_KEYS, Database
from podborka.metrics import score
from podborka.models import ContentType, Platform, Query
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
SORT_LABELS = {
    "velocity": "Скорость",
    "engagement": "Вовлечённость",
    "reactions": "Реакции",
    "published": "Новые",
}
assert SORT_LABELS.keys() == SORT_KEYS.keys()


def split_list(raw: str) -> list[str]:
    """«a, b\\nc» → ['a', 'b', 'c']"""
    parts = (p.strip() for line in raw.splitlines() for p in line.split(","))
    return [p for p in parts if p]


def create_app(db: Database, collectors: Sequence[Collector]) -> FastAPI:
    app = FastAPI(title="Podborka", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    fmt.register(templates.env)
    templates.env.globals.update(
        Platform=Platform,
        ContentType=ContentType,
        PERIOD_LABELS=PERIOD_LABELS,
        SORT_LABELS=SORT_LABELS,
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
    def index(request: Request):
        now = datetime.now(UTC)
        trends = [query_trend(db, q, now=now) for q in db.list_queries()]
        trends.sort(key=lambda t: (t.mentions_recent, t.growth or 0), reverse=True)
        top = db.ranked_items(sort="velocity", limit=8, since=now - timedelta(days=7))
        names = {q.id: q.name for q in db.list_queries()}
        return page(request, "index.html", trends=trends, top=top, names=names)

    # --- лента -------------------------------------------------------------

    @app.get("/items", response_class=HTMLResponse)
    def items(
        request: Request,
        query_id: str = "",
        platform: str = "",
        content_type: str = "",
        period: str = "7d",
        sort: str = "velocity",
    ):
        filters = {
            "query_id": int(query_id) if query_id.isdigit() else None,
            "platform": Platform(platform) if platform in Platform else None,
            "content_type": ContentType(content_type) if content_type in ContentType else None,
        }
        period = period if period in PERIODS else "7d"
        sort = sort if sort in SORT_KEYS else "velocity"
        if PERIODS[period]:
            filters["since"] = datetime.now(UTC) - PERIODS[period]
        ranked = db.ranked_items(sort=sort, limit=200, **filters)
        ctx = {
            "ranked": ranked,
            "queries": db.list_queries(),
            "f": {
                "query_id": query_id,
                "platform": platform,
                "content_type": content_type,
                "period": period,
                "sort": sort,
            },
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
        return page(request, "queries.html", queries=db.list_queries(), edit=None)

    @app.get("/queries/{query_id}/edit", response_class=HTMLResponse)
    def query_edit(request: Request, query_id: int):
        return page(
            request, "queries.html", queries=db.list_queries(), edit=get_query_or_404(query_id)
        )

    @app.post("/queries")
    def query_save(
        name: Annotated[str, Form()],
        keywords: Annotated[str, Form()] = "",
        hashtags: Annotated[str, Form()] = "",
        accounts: Annotated[str, Form()] = "",
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
