"""v10 신고가 거감짜름 — 검출 로직 단위테스트."""
import numpy as np
import pandas as pd

from swing_trader.config import load_config
from swing_trader.strategy import v10_new_high as v10


def test_v10_config_defaults():
    cfg = load_config()
    assert cfg.get("v10", "high_n") == 252
    assert cfg.get("v10", "vol_x") == 2.0
    assert cfg.get("v10", "window") == 3
    assert cfg.get("v10", "vol_dry") == 0.7
    assert cfg.get("v10", "supply_days") == 3
    assert cfg.get("v10", "supply_required") is True
    assert cfg.get("v10", "regime_gate") is True
    assert cfg.get("v10", "regime_ma") == 50
    assert cfg.get("v10", "min_tv_eok") == 50


def _df(closes, opens=None, vols=None):
    n = len(closes)
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    c = np.array(closes, dtype=float)
    o = np.array(opens, dtype=float) if opens is not None else np.r_[c[0], c[:-1]]
    v = np.array(vols, dtype=float) if vols is not None else np.full(n, 1e6)
    hi = np.maximum(o, c) * 1.001
    lo = np.minimum(o, c) * 0.999
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": v}, index=idx)


def test_breakout_mask_flags_new_high_bullish_volume():
    # 260봉 완만한 박스(100 근처) 뒤, 마지막 봉에서 신고가 장대양봉 + 대량거래.
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)      # 98~102 박스
    closes = base + [110.0]                                     # 신고가 돌파
    opens = [c for c in closes]
    opens[-1] = 105.0                                           # 장대양봉(+4.8% 몸통)
    vols = [1e6] * 260 + [3e6]                                  # 돌파일 3배
    df = _df(closes, opens, vols)
    m = v10.breakout_mask(df, high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0)
    assert m[-1]                     # 마지막 봉 = 돌파
    assert not m[:-1].any()          # 박스 구간은 돌파 아님


def test_breakout_mask_rejects_low_volume():
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    closes = base + [110.0]
    opens = list(closes); opens[-1] = 105.0
    vols = [1e6] * 261                                          # 돌파일 거래량 증가 없음
    df = _df(closes, opens, vols)
    m = v10.breakout_mask(df, high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0)
    assert not m[-1]
