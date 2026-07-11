"""v10 라이브 — 오늘 거감짜름 진입 신호 빌더.

run_once 의 KR 사이클을 본떠, 진입 신호 소스만 SignalEngine(노트) → v10 전시장 스캔으로 교체.
청산/사이징/영속화는 기존 PositionManager/OrderManager/analytics/briefer 재사용.
"""
from __future__ import annotations

from ..market.supply import supply_ok
from ..models import Signal, SignalKind
from . import risk as risk_mod
from .v10_new_high import _params_from_cfg, regime_ok, scan_candidates


def build_v10_signals(cfg, panel: dict, d: str, supply, kospi_up, kosdaq_up,
                      market_of: dict) -> list[Signal]:
    """오늘(d) 거감짜름 진입 후보 중 라이브 게이트 통과분을 매수 Signal 로.

    수급: 라이브 페일오픈(None=데이터없음 → 진입 허용, False=순매도 확정 → 차단).
    시황: regime_ok(up 집합 None → 페일오픈). 룩어헤드 없음(entry_date==d 후보만, ≤d 데이터).
    """
    p = _params_from_cfg(cfg)
    stop_pct = float(cfg.get("risk", "default_stop_pct", default=-3.0))
    take1_pct = float(cfg.get("risk", "take1_pct", default=6.0))
    out: list[Signal] = []
    for ticker, df in panel.items():
        if df is None or len(df) < p["high_n"] + p["window"] + 5:
            continue
        cands = scan_candidates(
            df, ticker, high_n=p["high_n"], vol_x=p["vol_x"], body_min=p["body_min"],
            min_tv_eok=p["min_tv_eok"], window=p["window"], vol_dry=p["vol_dry"], body_max=p["body_max"])
        for c in cands:
            if c.entry_date != d:
                continue
            market = market_of.get(ticker, "KOSPI")
            if not regime_ok(market, d, kospi_up, kosdaq_up):
                continue
            netbuy = supply.institution_netbuy(ticker) if supply is not None else None
            if supply_ok(netbuy, d, p["supply_days"]) is False:   # 명시적 순매도만 차단(None=페일오픈)
                continue
            plan = risk_mod.build_plan(c.entry_price, default_stop_pct=stop_pct, take1_pct=take1_pct)
            score = 80.0 + (5.0 if c.all_time else 0.0) + (3.0 if c.hist_vol else 0.0)
            out.append(Signal(
                ticker=ticker, name=ticker, kind=SignalKind.BUY, score=score,
                price=c.entry_price, plan=plan, sector=None,
                reasons=[f"v10 거감짜름 진입(d={d})",
                         *(["역사적 신고가"] if c.all_time else []),
                         *(["역사적 거래량"] if c.hist_vol else [])],
            ))
    return out
