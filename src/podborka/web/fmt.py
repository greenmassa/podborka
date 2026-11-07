"""Фильтры Jinja для чисел и времени по-русски."""

from __future__ import annotations

from datetime import UTC, datetime

from jinja2 import Environment

from podborka.models import ContentType, Platform

PLATFORM_LABELS = {
    Platform.YOUTUBE: "YouTube",
    Platform.INSTAGRAM: "Instagram",
    Platform.X: "X",
    Platform.TELEGRAM: "Telegram",
    Platform.TIKTOK: "TikTok",
}
CONTENT_LABELS = {ContentType.POST: "Пост", ContentType.VIDEO: "Видео"}


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def num(value: float | int | None) -> str:
    """12345 → «12,3 тыс», 2_500_000 → «2,5 млн»."""
    if value is None:
        return "—"
    v = float(value)
    for limit, suffix in ((1e9, " млрд"), (1e6, " млн"), (1e3, " тыс")):
        if abs(v) >= limit:
            s = f"{v / limit:.1f}".rstrip("0").rstrip(".")
            return s.replace(".", ",") + suffix
    if v != int(v) and abs(v) < 10:
        return f"{v:.1f}".replace(".", ",")
    return str(int(round(v)))


def pct(value: float | None) -> str:
    if value is None:
        return "—"
    p = value * 100
    s = f"{p:.1f}" if p < 10 else f"{p:.0f}"
    return s.replace(".", ",") + "%"


def growth(value: float | None) -> str:
    if value is None:
        return "новое"
    return "×" + f"{value:.1f}".replace(".", ",")


def ago(dt: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    minutes = int((now - dt).total_seconds() // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    return f"{days} {plural(days, 'день', 'дня', 'дней')} назад"


def dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def register(env: Environment) -> None:
    env.filters.update(num=num, pct=pct, growth=growth, ago=ago, dt=dt)
    env.globals.update(
        plural=plural, PLATFORM_LABELS=PLATFORM_LABELS, CONTENT_LABELS=CONTENT_LABELS
    )
