"""v10 — 신고가 거감짜름 기법(보컬 김영준). 순수 검출 + 전시장 오케스트레이션.

진입: 52주 신고가 장대양봉 대량거래 돌파(B) → 다음 window봉 내 첫 '거감짜름'(거래량 마름 짧은음봉 D)
      종가 매수. 기관 연속 순매수 하드게이트 + 코스닥/코스피 50일선 시황게이트.
청산: v7 재사용(5일선 이탈/대량음봉/손절/max_hold).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..market.supply import supply_ok
from .backtest import _v7_exit
from .harness import Trade


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


@dataclass
class Candidate:
    ticker: str
    breakout: str        # 돌파일 'YYYY-MM-DD'
    entry_date: str      # 거감짜름 진입일 'YYYY-MM-DD'
    entry_idx: int
    entry_price: float   # 진입일 종가
    all_time: bool       # 역사적 신고가 가점
    hist_vol: bool       # 역사적 거래량 가점


def scan_candidates(df: pd.DataFrame, ticker: str, *, high_n: int, vol_x: float,
                    body_min: float, min_tv_eok: float, window: int,
                    vol_dry: float, body_max: float) -> list[Candidate]:
    """돌파봉마다 거감짜름 진입봉을 찾아 Candidate 생성. 룩어헤드 없음(≤진입봉 데이터만)."""
    bmask = breakout_mask(df, high_n=high_n, vol_x=vol_x, body_min=body_min, min_tv_eok=min_tv_eok)
    ath = all_time_high_mask(df)
    hvol = hist_vol_mask(df)
    c = _arr(df, "close")
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    out: list[Candidate] = []
    i = 0
    n = len(c)
    while i < n:
        if bmask[i]:
            j = find_geogamjjareum(df, i, window=window, vol_dry=vol_dry, body_max=body_max)
            if j is not None:
                out.append(Candidate(ticker, dates[i], dates[j], j, float(c[j]),
                                     bool(ath[i]), bool(hvol[i])))
                i = j + 1                    # 진입 후 다음 돌파부터 재스캔
                continue
        i += 1
    return out


def index_up_days(index_code: str, ma: int, reader=None) -> "set[str] | None":
    """지수(KS11/KQ11) 종가 ≥ ma일선인 날짜 집합. 실패 시 None(페일오픈)."""
    try:
        if reader is None:
            import FinanceDataReader as fdr
            reader = lambda code: fdr.DataReader(code, "2023-01-01")["Close"].astype(float)
        s = reader(index_code)
        up = s >= s.rolling(ma).mean()
        return {d.strftime("%Y-%m-%d") for d, u in up.items() if bool(u)}
    except Exception:  # noqa: BLE001
        return None


def regime_ok(market: str, date: str, kospi_up, kosdaq_up) -> bool:
    """시장별 국면 게이트. up집합 None(데이터 없음)이면 페일오픈(True)."""
    up = kosdaq_up if str(market).upper() == "KOSDAQ" else kospi_up
    if up is None:
        return True
    return date in up


def ticker_trades(df, ticker, market, netbuy, kospi_up, kosdaq_up, *,
                  params: dict, mode: str, cost: float) -> list[Trade]:
    """한 종목의 v10 거래 목록. 수급/시황 게이트 후 v7 청산으로 수익률 산출."""
    cands = scan_candidates(
        df, ticker, high_n=params["high_n"], vol_x=params["vol_x"],
        body_min=params["body_min"], min_tv_eok=params["min_tv_eok"],
        window=params["window"], vol_dry=params["vol_dry"], body_max=params["body_max"])
    if not cands:
        return []
    close = _arr(df, "close"); open_ = _arr(df, "open"); vol = _arr(df, "volume")
    ma5 = pd.Series(close).rolling(5, min_periods=1).mean().to_numpy()
    va20 = pd.Series(vol).rolling(20, min_periods=5).mean().to_numpy()
    out: list[Trade] = []
    for cand in cands:
        if not regime_ok(market, cand.entry_date, kospi_up, kosdaq_up):
            continue
        ok = supply_ok(netbuy, cand.entry_date, params["supply_days"])
        if ok is False:
            continue
        if ok is None and mode == "backtest":     # 하드게이트: 미검증 드롭
            continue
        # ok is True, 또는 (ok is None and mode=="live") → 진입(페일오픈)
        ret, _jend = _v7_exit(close, open_, vol, ma5, va20, cand.entry_idx, cand.entry_price,
                              stop=params["stop"], take1=params["take1"],
                              volspike=params["volspike"], max_hold=params["max_hold"])
        out.append(Trade(ticker, cand.entry_date, float(ret) - cost))
    return out


def _params_from_cfg(cfg) -> dict:
    v = cfg.get("v10", default={})
    return dict(
        high_n=int(v.get("high_n", 252)), vol_x=float(v.get("vol_x", 2.0)),
        body_min=float(v.get("body_min", 0.03)), min_tv_eok=float(v.get("min_tv_eok", 50)),
        window=int(v.get("window", 3)), vol_dry=float(v.get("vol_dry", 0.7)),
        body_max=float(v.get("body_max", 0.03)), supply_days=int(v.get("supply_days", 3)),
        stop=float(cfg.get("risk", "default_stop_pct", default=-3.0)) / 100,
        take1=None, volspike=2.5,
        max_hold=int(cfg.get("risk", "max_hold_days", default=40)))


def v10_market_trades(panel: dict, market_of: dict, supply, cfg, *,
                      mode: str = "backtest", kospi_up=None, kosdaq_up=None) -> list[Trade]:
    """전시장 패널 v10 리플레이. 후보 있는 종목에만 수급 조회(접근법 A)."""
    params = _params_from_cfg(cfg)
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    cost = 2 * (fee + slip)
    out: list[Trade] = []
    for ticker, df in panel.items():
        if df is None or len(df) < params["high_n"] + params["window"] + 5:
            continue
        # 1단계: 값싼 가격 셋업으로 후보 유무만 확인(수급 조회 전)
        cands = scan_candidates(
            df, ticker, high_n=params["high_n"], vol_x=params["vol_x"],
            body_min=params["body_min"], min_tv_eok=params["min_tv_eok"],
            window=params["window"], vol_dry=params["vol_dry"], body_max=params["body_max"])
        if not cands:
            continue
        # 2단계: 후보 종목만 수급 조회
        netbuy = supply.institution_netbuy(ticker) if supply is not None else None
        out += ticker_trades(df, ticker, market_of.get(ticker, "KOSPI"),
                             netbuy, kospi_up, kosdaq_up,
                             params=params, mode=mode, cost=cost)
    return out
