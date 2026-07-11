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
