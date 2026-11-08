# Подборка

Сервис находит товары, которые набирают популярность, и оценивает их цитируемость в соцсетях: YouTube, Instagram, X, Telegram, TikTok. Собирает посты, видео и упоминания по ключевым словам, хэштегам и @аккаунтам, считает вовлечённость и скорость набора реакций.

## Стек и структура

```
src/podborka/
  models.py        Query (товар), Item (пост), Snapshot (счётчики на момент сбора)
  metrics.py       вовлечённость, скорость, рост упоминаний, размер автора
  db.py            SQLite, ItemFilter, ranked_items; миграции колонок — MIGRATIONS
  service.py       run_query (сбор), query_trend (сводка по товару)
  collectors/      по модулю на соцсеть; demo.py — демо-сборщик
  web/             FastAPI + Jinja2 + htmx + Chart.js; шаблоны в templates/
tests/             pytest
```

Python 3.12+, зависимости через `uv`.

## Команды

- `uv run pytest` — тесты
- `uv run ruff check . && uv run ruff format --check .` — линтер и формат
- `uv run python -m podborka seed` — демо-данные
- `uv run python -m podborka serve --demo` — веб-интерфейс на http://127.0.0.1:8000

## Правила

- Одна фича → тесты + ruff → коммит. Перед большой задачей — короткий план.
- Метрики и фильтрация — в `metrics.py` / `db.py`, не в шаблонах.
- Новая колонка в существующей таблице — добавить в `SCHEMA` и в `MIGRATIONS`.
- Время везде с часовым поясом (UTC); наивные datetime база отклоняет.
- UI на русском. Mobile-first: 390px без горизонтального скролла, тач-таргеты ≥ 44px.
- Ключи API — только в `.env` (в `.gitignore`), не в коде и не в чате.

## Статус

Готово: каркас, веб-интерфейс, фильтры (на демо-данных). Дальше: сборщики YouTube → Telegram → Instagram/TikTok/X через Apify → автосбор и деплой.
