"""단타 백테스트 리플레이 — 각 거래일을 '전일 지표 → 당일 settle_item' 으로 재현.

기계룰만 검증한다(시나리오 필터는 과거 뉴스 재현 불가 → 라이브 그림자 A/B 담당).
ret 은 소수(harness.Trade 규약) — 대시보드 복리 곡선은 eq *= 1 + pfrac*ret.
"""
from __future__ import annotations

from ..strategy.harness import Trade
from .strategy import (V1_K, V1_STOP, V2_STOP, V3_STOP, V3_TARGET,
                       V4_STOP, V4_TARGET, V4_SURGE, V5_STOP, V5_TARGET,
                       PlanItem, settle_item, v3_setup_ok, v5_setup_ok)

_FEE, _SLIP = 1.5, 5.0    # config paper 기본과 동일(리플레이 고정 — 재현성)


def simulate_stock(ticker: str, df, min_tv_eok: float = 50.0) -> dict:
    out: dict = {"v1": [], "v2": [], "v3": [], "v4": [], "v5": []}
    if df is None or len(df) < 61:
        return out
    closes = df["close"]
    volumes = df["volume"] if "volume" in df else None
    for i in range(60, len(df)):
        prev, bar = df.iloc[i - 1], df.iloc[i]
        tv_eok = float(prev["close"]) * float(prev.get("volume", 0)) / 1e8
        if tv_eok < min_tv_eok:
            continue
        d = df.index[i].strftime("%Y-%m-%d")
        prev_range = float(prev["high"]) - float(prev["low"])
        base = dict(ticker=ticker, name=ticker, qty=1, prev_close=float(prev["close"]),
                    prev_range=prev_range)
        # v1 돌파 — 매일 시도(체결은 settle 이 판정)
        f = settle_item(PlanItem(model="v1", stop_pct=V1_STOP, k=V1_K, **base),
                        bar, _FEE, _SLIP)
        if f:
            out["v1"].append(Trade(ticker, d, f.ret_pct / 100))
        # v2 갭반등 — 전일 기준 20>60일선일 때만 후보(전일까지 데이터만 사용)
        ma20 = float(closes.iloc[i - 20:i].mean())
        ma60 = float(closes.iloc[i - 60:i].mean())
        if ma20 > ma60:
            f = settle_item(PlanItem(model="v2", stop_pct=V2_STOP, **base),
                            bar, _FEE, _SLIP)
            if f:
                out["v2"].append(Trade(ticker, d, f.ret_pct / 100))
        # v3 터닝포인트 갭반등 — v2 골격 + 50일선 흐름·VWMA50 지지 필터 + 타이트 손절/장중 익절
        if ma20 > ma60 and v3_setup_ok(closes.iloc[:i], volumes.iloc[:i] if volumes is not None else None):
            f = settle_item(PlanItem(model="v3", stop_pct=V3_STOP, target_pct=V3_TARGET, **base),
                            bar, _FEE, _SLIP)
            if f:
                out["v3"].append(Trade(ticker, d, f.ret_pct / 100))
        # v4 급등 후 첫 조정 반등 — 정배열 + 전일 급등(+5%↑) 다음날 갭하락(-2%) 조정에 시가 매수
        #   (추격 금지·눌림목 매수). 손절 -1.5% / 익절 +10% / 종가청산. OOS서 v3 대비 개선.
        surged = i >= 2 and float(closes.iloc[i - 2]) > 0 and \
            float(closes.iloc[i - 1]) >= float(closes.iloc[i - 2]) * (1 + V4_SURGE / 100)
        if ma20 > ma60 and surged:
            f = settle_item(PlanItem(model="v4", stop_pct=V4_STOP, target_pct=V4_TARGET, **base),
                            bar, _FEE, _SLIP)
            if f:
                out["v4"].append(Trade(ticker, d, f.ret_pct / 100))
        # v5 상따 모멘텀 — 전일 폭등(+15%↑)+대량거래(20일평균×5)+신고가 종목을 다음날 시가 매수
        #   (이가근 상따의 일봉 정직 버전). 갭 범위·손절·익절은 settle 이 판정. IS 그리드 선택.
        if volumes is not None and v5_setup_ok(closes.iloc[:i], volumes.iloc[:i]):
            f = settle_item(PlanItem(model="v5", stop_pct=V5_STOP, target_pct=V5_TARGET, **base),
                            bar, _FEE, _SLIP)
            if f:
                out["v5"].append(Trade(ticker, d, f.ret_pct / 100))
    return out
