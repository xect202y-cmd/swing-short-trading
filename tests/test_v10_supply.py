"""v10 기관 수급 — 네이버 frgn 파서 + 수급 게이트."""
from pathlib import Path

import pandas as pd

from swing_trader.market.supply import parse_frgn_html

FIX = Path(__file__).parent / "fixtures" / "frgn_005930.html"


def test_parse_frgn_html_returns_institution_series():
    html = FIX.read_bytes().decode("euc-kr", "replace")
    s = parse_frgn_html(html)
    assert isinstance(s, pd.Series)
    assert len(s) >= 5                         # 페이지당 ~10거래일
    assert list(s.index) == sorted(s.index)    # 오름차순
    assert s.index[0].count("-") == 2          # 'YYYY-MM-DD'
    assert s.dtype == float


def test_parse_frgn_html_empty_on_garbage():
    assert parse_frgn_html("<html><body>no table</body></html>").empty


# Tests for supply_ok (Step 1: Failing tests)
from swing_trader.market.supply import supply_ok


def _series(pairs):
    return pd.Series({d: float(v) for d, v in pairs})


def test_supply_ok_true_on_consecutive_buying():
    s = _series([("2026-07-06", 100), ("2026-07-07", 200), ("2026-07-08", 300),
                 ("2026-07-09", 400)])
    assert supply_ok(s, "2026-07-09", 3) is True


def test_supply_ok_false_on_net_selling():
    s = _series([("2026-07-06", 100), ("2026-07-07", -500), ("2026-07-08", -600),
                 ("2026-07-09", -700)])
    assert supply_ok(s, "2026-07-09", 3) is False


def test_supply_ok_none_when_missing():
    assert supply_ok(None, "2026-07-09", 3) is None
    assert supply_ok(_series([("2026-07-09", 100)]), "2026-07-09", 3) is None   # 표본부족
