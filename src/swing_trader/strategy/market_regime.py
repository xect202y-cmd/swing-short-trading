"""시장 국면(regime) 판별 — 시장지수 일봉으로 날짜별 BULL/NEUTRAL/BEAR/CRASH.

과거전용(룩어헤드 없음): 날짜 t 의 regime 은 t 까지의 지수 데이터만 사용.
백테스트·라이브 공용. macro/regime.py(거시 텍스트노트)와 별개 — 이쪽은 가격 기반 이력.
"""
from __future__ import annotations

from enum import Enum

import pandas as pd


class Regime(str, Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    CRASH = "CRASH"


def _col1d(df, name):
    """OHLCV 컬럼을 1D 시리즈로 강제(provider df 의 MultiIndex/중복컬럼 → 2D 방어)."""
    col = df[name]
    if getattr(col, "ndim", 1) > 1:
        col = col.iloc[:, 0]
    return col.astype(float)


def classify_series(index_df, *, crash_dd: float = -0.12, crash_ret5: float = -0.08) -> dict:
    """지수 OHLCV → {'YYYY-MM-DD': Regime}. 우선순위 CRASH>BEAR>BULL>NEUTRAL."""
    close = _col1d(index_df, "close")
    high = _col1d(index_df, "high")
    ma50 = close.rolling(50, min_periods=50).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    dd60 = close / high.rolling(60, min_periods=1).max() - 1.0
    ret5 = close.pct_change(5)
    slope50 = ma50 - ma50.shift(20)
    out: dict = {}
    for i in range(len(close)):
        d = close.index[i].strftime("%Y-%m-%d")
        c = close.iloc[i]
        m50, m200 = ma50.iloc[i], ma200.iloc[i]
        _dd, _r5, _sl = dd60.iloc[i], ret5.iloc[i], slope50.iloc[i]
        if (pd.notna(_dd) and _dd <= crash_dd) or (pd.notna(_r5) and _r5 <= crash_ret5):
            out[d] = Regime.CRASH
        elif pd.notna(m200) and c < m200 and pd.notna(_sl) and _sl < 0:
            out[d] = Regime.BEAR
        elif pd.notna(m200) and c > m200 and m50 > m200 and c > m50:
            out[d] = Regime.BULL
        else:
            out[d] = Regime.NEUTRAL
    return out
