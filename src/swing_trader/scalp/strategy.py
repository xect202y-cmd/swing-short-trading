"""단타(데이트레이딩) 룰 — v1 변동성돌파(추세형)·v2 갭하락반등(역추세형).

일봉 OHLC 만으로 정직하게 판정한다:
- v1 트리거는 당일 시가 앵커(Larry Williams 방식) = 시가 + k×전일레인지
  → 트리거는 항상 시가보다 크므로 갭 상방 체결 케이스는 존재하지 않는다.
- 체결 인정 = 트리거가가 당일 고저 범위 안일 때만 (look-ahead 금지)
- 손절가는 진입가 앵커 = entry × (1 + stop_pct/100)
- 손절 우선 = 당일 저가가 손절가 이하이면 손절 체결로 보수 판정(장중 순서 미상)
- 전량 당일 종가 청산(오버나잇 없음)
"""
from __future__ import annotations

from dataclasses import dataclass

V1_K = 0.5        # 돌파 계수: 시가 + k×전일레인지
V1_STOP = -2.0    # %
V2_GAP = -2.0     # 시가 갭하락 임계(%)
V2_STOP = -2.5    # %


@dataclass(frozen=True)
class PlanItem:
    model: str                 # "v1" | "v2"
    ticker: str
    name: str
    qty: int
    stop_pct: float
    prev_close: float
    prev_range: float          # 전일 고가-저가
    k: float | None = None     # v1 전용
    trigger: float | None = None   # 표시용(KR은 실시간 시가로 해석) — 정산은 확정 시가로 재계산
    why: str = ""
    shadow: bool = False       # 시나리오 필터 OFF(그림자 A/B) 항목


@dataclass(frozen=True)
class Fill:
    entry: float
    exit: float
    pnl: float
    ret_pct: float
    reason: str    # "손절" | "종가청산"


def settle_item(item: PlanItem, bar, fee_bps: float, slip_bps: float) -> Fill | None:
    """확정 일봉으로 체결/청산 판정. None=미체결."""
    o, h, l, c = (float(bar["open"]), float(bar["high"]),
                  float(bar["low"]), float(bar["close"]))
    if o <= 0 or h <= 0:
        return None
    cost = (fee_bps + slip_bps) / 10000
    if item.model == "v1":
        trigger = o + (item.k or V1_K) * item.prev_range  # 당일 시가 앵커
        if h < trigger:
            return None
        entry = trigger * (1 + cost)
        stop_price = entry * (1 + item.stop_pct / 100)  # 손절가는 진입가 기준
    else:  # v2 — 시가 갭하락 재확인(계획 시점 실시간 시가와 무관하게 확정 시가가 정본)
        if item.prev_close <= 0 or o > item.prev_close * (1 + V2_GAP / 100):
            return None
        entry = o * (1 + cost)
        stop_price = entry * (1 + item.stop_pct / 100)  # 손절가는 진입가 기준
    if l <= stop_price:
        exit_px, reason = stop_price * (1 - cost), "손절"
    else:
        exit_px, reason = c * (1 - cost), "종가청산"
    pnl = (exit_px - entry) * item.qty
    return Fill(entry=entry, exit=exit_px, pnl=pnl,
                ret_pct=round((exit_px / entry - 1) * 100, 2), reason=reason)
