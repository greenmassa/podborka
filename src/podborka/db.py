"""Хранилище на SQLite.

Пост хранится один раз, даже если подходит под несколько запросов
(связь — таблица item_queries). Счётчики пишутся снимками при каждом сборе,
из них считается скорость набора реакций.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from podborka.metrics import ItemScore, score
from podborka.models import AuthorSize, ContentType, Item, Platform, Query, Snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '[]',
    hashtags    TEXT NOT NULL DEFAULT '[]',
    accounts    TEXT NOT NULL DEFAULT '[]',
    platforms   TEXT NOT NULL DEFAULT '[]',
    category    TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id             INTEGER PRIMARY KEY,
    platform       TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    content_type   TEXT NOT NULL,
    url            TEXT NOT NULL,
    author         TEXT NOT NULL,
    text           TEXT NOT NULL,
    published_at   TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    UNIQUE (platform, external_id)
);

CREATE TABLE IF NOT EXISTS item_queries (
    item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    query_id  INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, query_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id                INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    captured_at       TEXT NOT NULL,
    views             INTEGER,
    likes             INTEGER,
    comments          INTEGER,
    shares            INTEGER,
    author_followers  INTEGER
);

CREATE INDEX IF NOT EXISTS snapshots_item ON snapshots(item_id, captured_at);
CREATE INDEX IF NOT EXISTS items_published ON items(published_at);
"""


def _ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime должен быть с часовым поясом")
    return dt.astimezone(UTC).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


# Колонки, добавленные после первой версии схемы: (таблица, колонка, тип).
MIGRATIONS = [("queries", "category", "TEXT")]


@dataclass
class ItemFilter:
    """Фильтры ленты. None / пусто — фильтр не применяется."""

    query_id: int | None = None
    category: str | None = None
    platforms: list[Platform] = field(default_factory=list)
    content_type: ContentType | None = None
    since: datetime | None = None  # опубликован не раньше
    first_seen_since: datetime | None = None  # впервые найден не раньше
    text: str | None = None  # подстрока в тексте, без учёта регистра
    author_sizes: list[AuthorSize] = field(default_factory=list)
    min_reactions: int = 0
    min_views: int = 0
    min_velocity: float = 0


@dataclass
class RankedItem:
    item: Item
    score: ItemScore
    snapshots: list[Snapshot]


SORT_KEYS = {
    "velocity": lambda r: r.score.velocity or 0,
    "engagement": lambda r: r.score.engagement or 0,
    "reactions": lambda r: r.score.reactions,
    "published": lambda r: r.item.published_at,
}


class Database:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # SQLite LOWER/LIKE не понимают кириллицу — свой casefold.
        self.conn.create_function(
            "casefold", 1, lambda v: v.casefold() if isinstance(v, str) else v, deterministic=True
        )
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, column, kind in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                with self.conn:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def close(self) -> None:
        self.conn.close()

    # --- запросы -----------------------------------------------------------

    def save_query(self, q: Query) -> Query:
        data = (
            q.name,
            json.dumps(q.keywords, ensure_ascii=False),
            json.dumps(q.hashtags, ensure_ascii=False),
            json.dumps(q.accounts, ensure_ascii=False),
            json.dumps([str(p) for p in q.platforms]),
            (q.category or "").strip() or None,
            int(q.active),
        )
        with self.conn:
            if q.id is None:
                q.created_at = q.created_at or datetime.now(UTC)
                cur = self.conn.execute(
                    "INSERT INTO queries (name, keywords, hashtags, accounts, platforms, category,"
                    " active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*data, _ts(q.created_at)),
                )
                q.id = cur.lastrowid
            else:
                self.conn.execute(
                    "UPDATE queries SET name=?, keywords=?, hashtags=?, accounts=?, platforms=?,"
                    " category=?, active=? WHERE id=?",
                    (*data, q.id),
                )
        return q

    def delete_query(self, query_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM queries WHERE id=?", (query_id,))

    @staticmethod
    def _query(row: sqlite3.Row) -> Query:
        return Query(
            id=row["id"],
            name=row["name"],
            keywords=json.loads(row["keywords"]),
            hashtags=json.loads(row["hashtags"]),
            accounts=json.loads(row["accounts"]),
            platforms=[Platform(p) for p in json.loads(row["platforms"])],
            category=row["category"],
            active=bool(row["active"]),
            created_at=_dt(row["created_at"]),
        )

    def get_query(self, query_id: int) -> Query | None:
        row = self.conn.execute("SELECT * FROM queries WHERE id=?", (query_id,)).fetchone()
        return self._query(row) if row else None

    def list_queries(self, active_only: bool = False) -> list[Query]:
        sql = "SELECT * FROM queries" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
        return [self._query(r) for r in self.conn.execute(sql)]

    def list_categories(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM queries WHERE category IS NOT NULL ORDER BY category"
        )
        return [r[0] for r in rows]

    # --- посты и снимки ----------------------------------------------------

    def upsert_item(self, item: Item, query_id: int, seen_at: datetime) -> Item:
        """Сохраняет пост (или обновляет текст/автора) и привязывает к запросу."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO items (platform, external_id, content_type, url, author, text,"
                " published_at, first_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (platform, external_id) DO UPDATE SET"
                " url=excluded.url, author=excluded.author, text=excluded.text",
                (
                    str(item.platform),
                    item.external_id,
                    str(item.content_type),
                    item.url,
                    item.author,
                    item.text,
                    _ts(item.published_at),
                    _ts(seen_at),
                ),
            )
            row = self.conn.execute(
                "SELECT id, first_seen_at FROM items WHERE platform=? AND external_id=?",
                (str(item.platform), item.external_id),
            ).fetchone()
            self.conn.execute(
                "INSERT OR IGNORE INTO item_queries (item_id, query_id) VALUES (?, ?)",
                (row["id"], query_id),
            )
        item.id = row["id"]
        item.first_seen_at = _dt(row["first_seen_at"])
        return item

    def add_snapshot(self, item_id: int, s: Snapshot) -> Snapshot:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO snapshots (item_id, captured_at, views, likes, comments, shares,"
                " author_followers) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    _ts(s.captured_at),
                    s.views,
                    s.likes,
                    s.comments,
                    s.shares,
                    s.author_followers,
                ),
            )
        s.id, s.item_id = cur.lastrowid, item_id
        return s

    @staticmethod
    def _item(row: sqlite3.Row) -> Item:
        return Item(
            id=row["id"],
            platform=Platform(row["platform"]),
            external_id=row["external_id"],
            content_type=ContentType(row["content_type"]),
            url=row["url"],
            author=row["author"],
            text=row["text"],
            published_at=_dt(row["published_at"]),
            first_seen_at=_dt(row["first_seen_at"]),
        )

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> Snapshot:
        return Snapshot(
            id=row["id"],
            item_id=row["item_id"],
            captured_at=_dt(row["captured_at"]),
            views=row["views"],
            likes=row["likes"],
            comments=row["comments"],
            shares=row["shares"],
            author_followers=row["author_followers"],
        )

    def get_item(self, item_id: int) -> Item | None:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return self._item(row) if row else None

    def get_snapshots(self, item_id: int) -> list[Snapshot]:
        rows = self.conn.execute(
            "SELECT * FROM snapshots WHERE item_id=? ORDER BY captured_at", (item_id,)
        )
        return [self._snapshot(r) for r in rows]

    def list_items(self, f: ItemFilter | None = None) -> list[Item]:
        """Посты по фильтрам, которые считаются в SQL (без метрик)."""
        f = f or ItemFilter()
        sql = "SELECT DISTINCT i.* FROM items i"
        where, args = [], []
        if f.query_id is not None or f.category is not None:
            sql += " JOIN item_queries iq ON iq.item_id = i.id JOIN queries q ON q.id = iq.query_id"
        if f.query_id is not None:
            where.append("q.id = ?")
            args.append(f.query_id)
        if f.category is not None:
            where.append("q.category = ?")
            args.append(f.category)
        if f.platforms:
            where.append(f"i.platform IN ({', '.join('?' * len(f.platforms))})")
            args.extend(str(p) for p in f.platforms)
        if f.content_type is not None:
            where.append("i.content_type = ?")
            args.append(str(f.content_type))
        if f.since is not None:
            where.append("i.published_at >= ?")
            args.append(_ts(f.since))
        if f.first_seen_since is not None:
            where.append("i.first_seen_at >= ?")
            args.append(_ts(f.first_seen_since))
        if f.text and f.text.strip():
            where.append("instr(casefold(i.text), ?) > 0")
            args.append(f.text.strip().casefold())
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [self._item(r) for r in self.conn.execute(sql, args)]

    def ranked_items(
        self, f: ItemFilter | None = None, sort: str = "velocity", limit: int = 100
    ) -> list[RankedItem]:
        """Посты с метриками по фильтрам, по убыванию выбранной метрики."""
        if sort not in SORT_KEYS:
            raise ValueError(f"неизвестная сортировка: {sort}")
        f = f or ItemFilter()
        ranked = []
        for item in self.list_items(f):
            snaps = self.get_snapshots(item.id)
            sc = score(snaps, item.published_at)
            if sc is None:
                continue
            if f.author_sizes and sc.author_size not in f.author_sizes:
                continue
            if sc.reactions < f.min_reactions or (sc.views or 0) < f.min_views:
                continue
            if (sc.velocity or 0) < f.min_velocity:
                continue
            ranked.append(RankedItem(item, sc, snaps))
        ranked.sort(key=SORT_KEYS[sort], reverse=True)
        return ranked[:limit]
