"""포트폴리오 노출 계산 + 브로커 시세 스칼라 계약.

회귀: price_fn 이 pandas Series 를 주면 PaperBroker.get_price 가 스칼라(float)로
강제해야 한다. 안 그러면 Exposure 의 'price_fn(t) or avg_price' 에서
ValueError(truth value of a Series is ambiguous) 로 run-once 가 크래시 →
Daily 브리핑 미발송으로 이어졌다.
"""
import tempfile
from pathlib import Path

import pandas as pd

from swing_trader.broker.paper import PaperBroker
from swing_trader.models import Position
from swing_trader.strategy import portfolio as pf


def _broker_with_position(price_fn):
    tmp = Path(tempfile.mkdtemp()) / "state.json"
    b = PaperBroker(seed_cash=5_000_000, state_path=tmp, price_fn=price_fn)
    b._positions["105560"] = Position(
        ticker="105560", name="KB금융", quantity=10, avg_price=158_379.0,
        stop=153_551.0, target1=166_215.0, sector="금융/은행",
    )
    return b


def test_get_price_coerces_series_to_float():
    b = _broker_with_position(lambda s: pd.Series([100.0, 158_300.0]))
    v = b.get_price("105560")
    assert isinstance(v, float)
    assert v == 158_300.0


def test_get_price_none_passthrough():
    b = _broker_with_position(lambda s: None)
    assert b.get_price("105560") is None


def test_exposure_with_series_price_fn_does_not_crash():
    # 과거엔 여기서 ValueError(Series truth value) 로 크래시했음.
    b = _broker_with_position(lambda s: pd.Series([158_300.0]))
    exp = pf.Exposure(5_000_000.0, b.get_positions(), b.get_price)
    assert exp.holdings == 158_300.0 * 10
