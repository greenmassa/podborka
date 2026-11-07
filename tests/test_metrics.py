from datetime import UTC, datetime, timedelta

import pytest

from podborka.metrics import engagement_rate, mention_growth, score, velocity
from podborka.models import Snapshot

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def snap(hours: float, **kw) -> Snapshot:
    return Snapshot(captured_at=T0 + timedelta(hours=hours), **kw)


class TestEngagement:
    def test_by_views(self):
        s = snap(1, views=1000, likes=40, comments=5, shares=5)
        assert engagement_rate(s) == pytest.approx(0.05)

    def test_falls_back_to_followers_without_views(self):
        s = snap(1, likes=90, comments=10, author_followers=2000)
        assert engagement_rate(s) == pytest.approx(0.05)

    def test_zero_views_falls_back_to_followers(self):
        s = snap(1, views=0, likes=10, author_followers=100)
        assert engagement_rate(s) == pytest.approx(0.1)

    def test_none_without_base(self):
        assert engagement_rate(snap(1, likes=10)) is None


class TestVelocity:
    def test_empty(self):
        assert velocity([], T0) is None

    def test_single_snapshot_uses_age(self):
        assert velocity([snap(4, likes=100)], T0) == pytest.approx(25)

    def test_single_fresh_snapshot_is_capped(self):
        # 6 минут от публикации: делим на 0.5 ч, а не на 0.1
        assert velocity([snap(0.1, likes=10)], T0) == pytest.approx(20)

    def test_two_snapshots_use_delta(self):
        snaps = [snap(2, likes=100), snap(5, likes=160, comments=20, shares=10)]
        assert velocity(snaps, T0) == pytest.approx(30)

    def test_order_does_not_matter(self):
        snaps = [snap(5, likes=160), snap(1, likes=10), snap(2, likes=100)]
        assert velocity(snaps, T0) == pytest.approx(20)

    def test_deleted_reactions_do_not_go_negative(self):
        assert velocity([snap(1, likes=100), snap(2, likes=90)], T0) == 0


def test_mention_growth():
    now = T0
    published = [now - timedelta(hours=h) for h in (1, 2, 3, 30, 50)]
    assert mention_growth(published, now) == (3, 1, 3.0)


def test_mention_growth_without_previous():
    assert mention_growth([T0 - timedelta(hours=1)], T0) == (1, 0, None)


def test_score_uses_last_snapshot():
    snaps = [snap(1, views=100, likes=10), snap(2, views=1000, likes=50)]
    sc = score(snaps, T0)
    assert sc.reactions == 50
    assert sc.views == 1000
    assert sc.engagement == pytest.approx(0.05)
    assert sc.velocity == pytest.approx(40)
