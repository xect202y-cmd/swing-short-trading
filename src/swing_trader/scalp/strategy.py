"""단타(데이트레이딩) 룰 — v1 변동성돌파(추세형)·v2 갭하락반등(역추세형)·v3 터닝포인트 갭반등.

v3 (2026-07-04, '불장단타왕' 기법 반영): v2의 갭하락 반등 골격을 유지하되
- 진입은 '리턴 구간'에서만: 이평선 흐름(50일선 기울기 비음)·거래량 가중 이평(VWMA50) 지지 위
- 손익비 원칙: 타이트 손절 + 장중 익절 목표(저가/고가 동시 터치 시 손절 우선 보수 판정)

일봉 OHLC 만으로 정직하게 판정한다:
- v1 트리거는 당일 시가 앵커(Larry Williams 방식) = 시가 + k×전일레인지
  → 트리거는 항상 시가보다 크므로 갭 상방 체결 케이스는 존재하지 않는다.
- 체결 인정 = 트리거가가 당일 고저 범위 안일 때만 (look-ahead 금지)
- 손절 판정은 모델별로 다르다(2026-07-03 500일 실증으로 확정):
  · v1(장중 트리거 진입): 당일 저가가 진입 '전'(아침 눌림)일 수 있어 저가 기반
    손절 판정이 승자를 손절로 오판(기대값 -1.2%p 왜곡, 승률 17%→44%) → 일봉으로는
    손절 시뮬 불가. 종가 청산만 인정(stop_pct 는 라이브 가이드 표시용).
  · v2(시가 진입): 저가는 항상 진입 이후 → 저가 ≤ 손절가(진입가 앵커)면 손절 체결.
- 전량 당일 종가 청산(오버나잇 없음)
"""
from __future__ import annotations

from dataclasses import dataclass

V1_K = 0.5        # 돌파 계수: 시가 + k×전일레인지
V1_STOP = -2.0    # %
V2_GAP = -2.0     # 시가 갭하락 임계(%)
V2_STOP = -2.5    # %
V3_GAP = -2.0     # v3 시가 갭하락 임계(%) — v2 골격 유지
V3_STOP = -1.0    # v3 타이트 손절(%) — 손익비 원칙(영상: 1% 손절/5~7% 익절). IS 그리드 선택
V3_TARGET = 7.0   # v3 장중 익절 목표(%) — RR 7:1
# v4 (2026-07-05, CIS 순행·Aziz ABCD·강창권 눌림목 3편 수렴): '급등 후 첫 조정(눌림목)' 반등.
V4_GAP = -2.0     # v4 시가 갭하락(조정) 임계(%)
V4_SURGE = 5.0    # v4 전일 급등 임계(%) — 급등 다음날 첫 조정만 대상(추격 금지·눌림목 매수)
V4_STOP = -1.5    # v4 손절(%)
V4_TARGET = 10.0  # v4 장중 익절 목표(%) — 승자 크게(전고점/+10%)
# v5 (2026-07-05, 이가근 '한국형 모멘텀 투자' 상따 기법의 일봉 정직 버전): 폭등 모멘텀 지속.
#   상따 원전은 '상한가 도달 순간 매수'(장중)이나 일봉·전일계획 프레임에선 불가 →
#   저자 대안("장중이 무서우면 종가에라도 반드시 사라"·삼양식품 "그다음날은 사야만 했다")을 따라
#   '전일 폭등(상한가권)+평소 대비 대량거래+신고가(왼쪽 매물대 청정)' 종목을 다음날 시가 매수.
#   과도 갭업은 추격 금지, 갭하락 출발은 모멘텀 소멸로 보고 미진입. 상수는 IS 그리드 선택.
#   IS 그리드(2026-07-05, 실데이터 84종목×500일, 276조합): 손절 -3%가 상위 전부 지배,
#   익절은 +20 > +15 > +10, 갭 상한 +10 우세. 최상위 조합 채택(IS 기대값 +1.94%/건·PF 2.38).
V5_SURGE = 15.0   # v5 전일 폭등 임계(%) — 상한가권 강모멘텀만
V5_VOL_X = 5.0    # v5 전일 거래량 ≥ 직전 20일 평균 × N (영상: 평소 10배 거래 폭발)
V5_HIGH_N = 60    # v5 신고가 판정 구간(일) — 전일 종가가 구간 최고(매물대 청정)
V5_MIN_GAP = -3.0 # v5 시가 갭 하한(%) — 이보다 깊은 갭하락 출발은 모멘텀 소멸 → 미진입
V5_MAX_GAP = 10.0 # v5 시가 갭 상한(%) — 과도 갭업 추격 금지
V5_STOP = -3.0    # v5 손절(%) — 영상 '오후 2시 기법'의 -3% 손절과 일치
V5_TARGET = 20.0  # v5 장중 익절 목표(%) — 승자 크게(상따 폭발 수익 본질)


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
    target_pct: float | None = None   # v3 전용 — 장중 익절 목표(%)
    trigger: float | None = None   # 표시용(KR은 실시간 시가로 해석) — 정산은 확정 시가로 재계산
    why: str = ""
    shadow: bool = False       # 시나리오 필터 OFF(그림자 A/B) 항목


@dataclass(frozen=True)
class Fill:
    entry: float
    exit: float
    pnl: float
    ret_pct: float
    reason: str    # "손절" | "익절" | "종가청산"


def settle_item(item: PlanItem, bar, fee_bps: float, slip_bps: float) -> Fill | None:
    """확정 일봉으로 체결/청산 판정. None=미체결."""
    o, h, l, c = (float(bar["open"]), float(bar["high"]),
                  float(bar["low"]), float(bar["close"]))
    if o <= 0 or h <= 0:
        return None
    cost = (fee_bps + slip_bps) / 10000
    if item.model == "v1":
        trigger = o + (item.k if item.k is not None else V1_K) * item.prev_range  # 당일 시가 앵커
        if h < trigger:
            return None
        entry = trigger * (1 + cost)
        # v1 은 저가가 진입 전(아침 눌림)일 수 있어 손절 시뮬 불가 → 종가 청산만(모듈 docstring)
        exit_px, reason = c * (1 - cost), "종가청산"
    else:  # v2/v3/v4 갭하락 재확인 · v5 갭 범위 재확인(확정 시가가 정본)
        if item.prev_close <= 0:
            return None
        if item.model == "v5":
            gap_pct = (o / item.prev_close - 1) * 100
            if gap_pct < V5_MIN_GAP or gap_pct > V5_MAX_GAP:
                return None   # 갭하락(모멘텀 소멸)·과도 갭업(추격 금지) 모두 미진입
        else:
            gap = {"v3": V3_GAP, "v4": V4_GAP}.get(item.model, V2_GAP)
            if o > item.prev_close * (1 + gap / 100):
                return None
        entry = o * (1 + cost)
        stop_price = entry * (1 + item.stop_pct / 100)  # 시가 진입이라 저가 손절 판정 타당
        target_price = entry * (1 + item.target_pct / 100) if item.target_pct else None
        if l <= stop_price:      # 손절/익절 동시 터치 가능한 날은 손절 우선(보수 판정)
            exit_px, reason = stop_price * (1 - cost), "손절"
        elif target_price is not None and h >= target_price:
            exit_px, reason = target_price * (1 - cost), "익절"
        else:
            exit_px, reason = c * (1 - cost), "종가청산"
    pnl = (exit_px - entry) * item.qty
    return Fill(entry=entry, exit=exit_px, pnl=pnl,
                ret_pct=round((exit_px / entry - 1) * 100, 2), reason=reason)

def v5_setup_ok(closes, volumes) -> bool:
    """v5 '상따 모멘텀' 셋업 판정 — 전일까지의 시리즈만 사용(look-ahead 금지).

    - 전일 폭등: 종가 상승률 ≥ V5_SURGE (상한가권 강모멘텀)
    - 대량거래: 전일 거래량 ≥ 직전 20일 평균 × V5_VOL_X (시장이 인정한 재료)
    - 신고가: 전일 종가가 최근 V5_HIGH_N일 최고 종가 (왼쪽 매물대 청정)
    """
    if closes is None or volumes is None or len(closes) < V5_HIGH_N + 1 or len(volumes) < 22:
        return False
    pc, p2 = float(closes.iloc[-1]), float(closes.iloc[-2])
    if p2 <= 0 or pc < p2 * (1 + V5_SURGE / 100):
        return False
    vmean = float(volumes.iloc[-21:-1].mean())
    if vmean <= 0 or float(volumes.iloc[-1]) < V5_VOL_X * vmean:
        return False
    return pc >= float(closes.iloc[-V5_HIGH_N:].max())


def v3_setup_ok(closes, volumes) -> bool:
    """v3 '리턴 구간' 셋업 판정 — 전일까지의 시리즈만 사용(look-ahead 금지).

    - 50일선 흐름: 최근 50일 평균 ≥ 5거래일 전 50일 평균(하락 추세면 매매 회피, 완만/전환만)
    - VWMA50 지지: 전일 종가 ≥ 거래량 가중 50일 평균(거래량 실린 지지 위에서만)
    """
    if closes is None or len(closes) < 56:
        return False
    ma50_now = float(closes.iloc[-50:].mean())
    ma50_prev = float(closes.iloc[-55:-5].mean())
    if ma50_now < ma50_prev:
        return False
    if volumes is None or len(volumes) < len(closes):
        return False
    c = closes.iloc[-50:]
    v = volumes.iloc[-50:]
    denom = float(v.sum())
    if denom <= 0:
        return False
    vwma50 = float((c * v).sum()) / denom
    return float(closes.iloc[-1]) >= vwma50
