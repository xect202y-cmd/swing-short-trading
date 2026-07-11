"""v10 — 신고가 거감짜름 기법(보컬 김영준). 순수 검출 + 전시장 오케스트레이션.

진입: 52주 신고가 장대양봉 대량거래 돌파(B) → 다음 window봉 내 첫 '거감짜름'(거래량 마름 짧은음봉 D)
      종가 매수. 기관 연속 순매수 하드게이트 + 코스닥/코스피 50일선 시황게이트.
청산: v7 재사용(5일선 이탈/대량음봉/손절/max_hold).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _arr(df: pd.DataFrame, col: str) -> np.ndarray:
    s = df[col]
    if getattr(s, "ndim", 1) > 1:
        s = s.iloc[:, 0]
    return s.to_numpy(dtype=float)


def breakout_mask(df: pd.DataFrame, *, high_n: int, vol_x: float,
                  body_min: float, min_tv_eok: float) -> np.ndarray:
    """각 봉이 신고가 돌파 셋업(B)인지 bool 배열. 전일까지의 최고가를 당일 종가가 돌파."""
    c, o, v = _arr(df, "close"), _arr(df, "open"), _arr(df, "volume")
    prev_high = pd.Series(c).shift(1).rolling(high_n, min_periods=max(20, high_n // 2)).max().to_numpy()
    va20_prev = pd.Series(v).shift(1).rolling(20, min_periods=5).mean().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        body = np.where(o > 0, (c - o) / o, 0.0)
        tv_eok = c * v / 1e8
        m = ((c >= prev_high) & (c > o) & (body >= body_min)
             & (v >= vol_x * va20_prev) & (tv_eok >= min_tv_eok))
    return np.nan_to_num(m, nan=0.0).astype(bool)


def all_time_high_mask(df: pd.DataFrame) -> np.ndarray:
    """종가가 확보 이력 전체의 신고가(역사적 신고가 근사) — 가점 플래그."""
    c = _arr(df, "close")
    prev_max = pd.Series(c).shift(1).cummax().to_numpy()
    return np.nan_to_num(c >= prev_max, nan=0.0).astype(bool)


def hist_vol_mask(df: pd.DataFrame, ratio: float = 0.9) -> np.ndarray:
    """거래량이 확보 이력 전체 최고의 ratio 이상(역사적 거래량 근사) — 가점 플래그."""
    v = _arr(df, "volume")
    prev_vmax = pd.Series(v).shift(1).cummax().to_numpy()
    with np.errstate(invalid="ignore"):
        m = v >= prev_vmax * ratio
    return np.nan_to_num(m, nan=0.0).astype(bool)


def find_geogamjjareum(df: pd.DataFrame, breakout_idx: int, *,
                       window: int, vol_dry: float, body_max: float) -> int | None:
    """돌파봉 B 다음 window봉 내 '거감짜름'(음봉+거래량마름+짧은몸통+5일선유지) 첫 봉 인덱스."""
    c, o, v = _arr(df, "close"), _arr(df, "open"), _arr(df, "volume")
    ma5 = pd.Series(c).rolling(5, min_periods=1).mean().to_numpy()
    va20_raw = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()
    va20 = np.nan_to_num(va20_raw, nan=np.inf)
    vol_b = v[breakout_idx]
    n = len(c)
    for j in range(breakout_idx + 1, min(breakout_idx + window, n - 1) + 1):
        if o[j] <= 0:
            continue
        down = c[j] < o[j]
        dry = v[j] < va20[j] and v[j] < vol_b * vol_dry
        short = abs(c[j] - o[j]) / o[j] <= body_max
        trend = c[j] >= ma5[j]
        if down and dry and short and trend:
            return j
    return None
