"""Интерфейс сборщика. Каждая соцсеть реализует его в своём модуле."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from podborka.models import Collected, Platform, Query


class Collector(Protocol):
    platform: Platform

    def collect(self, query: Query) -> Iterable[Collected]:
        """Найти посты по запросу и вернуть их с текущими счётчиками."""
        ...
