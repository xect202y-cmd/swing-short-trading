"""OHLCV DataFrame → TechnicalSnapshot."""
from __future__ import annotations

import pandas as pd

from ..models import TechnicalSnapshot
from . import indicators as ind


def build_snapshot(ticker: str, df: pd.DataFrame, cfg_ind: dict) -> TechnicalSnapshot:
    """df 컬럼: open, high, low, close, volume (날짜 인덱스 오름차순)."""
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    price = float(close.iloc[-1])

    ma_windows = cfg_ind.get("ma_windows", [5, 20, 60])
    vol_windows = cfg_ind.get("vol_windows", [5, 20])
    rsi_period = cfg_ind.get("rsi_period", 14)

    ma = {w: round(float(ind.sma(close, w).iloc[-1]), 2) for w in ma_windows}
    vavg = {w: float(ind.vol_avg(vol, w).iloc[-1]) for w in vol_windows}
    rsi_v = float(ind.rsi(close, rsi_period).iloc[-1])
    macd_line, macd_sig = ind.macd(close)

    # 거래대금(억) ≈ 종가 * 5일평균거래량 / 1e8
    tv_eok = round(price * vavg.get(5, float(vol.iloc[-1])) / 1e8, 1)

    # ATR(14) — 변동성 기반 손절/목표 산출용
    high, low = df["high"].astype(float), df["low"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr_v = float(tr.rolling(14, min_periods=1).mean().iloc[-1])
    # 당일 갭(시가 vs 전일종가)
    today_open = float(df["open"].iloc[-1])
    gap = round((today_open - float(close.iloc[-2])) / float(close.iloc[-2]) * 100, 2) if len(df) >= 2 else None

    return TechnicalSnapshot(
        ticker=ticker,
        price=round(price, 2),
        ma=ma,
        rsi=round(rsi_v, 1),
        macd=round(float(macd_line.iloc[-1]), 3),
        macd_signal=round(float(macd_sig.iloc[-1]), 3),
        vol_avg={w: round(v, 0) for w, v in vavg.items()},
        vol_today=round(float(vol.iloc[-1]), 0),
        prev_high=round(float(df["high"].iloc[-2]), 2) if len(df) >= 2 else None,
        high_20=round(float(df["high"].tail(20).max()), 2),
        low_20=round(float(df["low"].tail(20).min()), 2),
        trading_value_eok=tv_eok,
        atr=round(atr_v, 2),
        atr_pct=round(atr_v / price * 100, 2) if price else None,
        gap_pct=gap,
        today_open=round(today_open, 2),
    )
