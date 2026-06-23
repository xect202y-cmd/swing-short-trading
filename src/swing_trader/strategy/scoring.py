"""스윙 후보 점수화 — 100점 만점 6항목. 실제 지표/노트값 기반(추측 최소화)."""
from __future__ import annotations

from ..models import RiskLevel, ScoreBreakdown, StockNote, TechnicalSnapshot


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def score_candidate(
    note: StockNote,
    tech: TechnicalSnapshot,
    macro_risk: RiskLevel,
    event_risk: RiskLevel,
    weights: dict[str, int],
    min_trading_value_eok: float = 30.0,
) -> ScoreBreakdown:
    b = ScoreBreakdown()
    price = tech.price

    # 1) 섹터/테마 모멘텀 (20) — 노트 상태/등급 + 추세(MA 정배열)
    w = weights.get("sector_momentum", 20)
    s = w * 0.5  # 기본
    reason = []
    if (note.grade or "").upper() in {"S", "A"}:
        s += w * 0.3
        reason.append(f"타점등급 {note.grade}")
    ma5, ma20, ma60 = tech.ma.get(5), tech.ma.get(20), tech.ma.get(60)
    if ma5 and ma20 and ma60 and ma5 >= ma20 >= ma60:
        s += w * 0.3
        reason.append("이평선 정배열")
    b.add("섹터/테마 모멘텀", _clamp(s, 0, w), w, ", ".join(reason) or "중립")

    # 2) 기술적 타점 (25) — 20일선 근처 반등 / 전일고가 돌파 / RSI 위치
    w = weights.get("technical_setup", 25)
    s = 0.0
    reason = []
    if tech.near_ma20:
        s += w * 0.4
        reason.append("20일선 근처(눌림)")
    elif tech.ma.get(20) and tech.price > tech.ma[20]:
        s += w * 0.2
        reason.append("20일선 상회(상승추세)")
    if tech.breakout_prev_high:
        s += w * 0.35
        reason.append("전일고가 돌파")
    if tech.rsi is not None and 45 <= tech.rsi <= 65:
        s += w * 0.25
        reason.append(f"RSI {tech.rsi}(과열 전)")
    elif tech.rsi is not None and tech.rsi > 75:
        reason.append(f"RSI {tech.rsi} 과열")
    b.add("기술적 타점", _clamp(s, 0, w), w, ", ".join(reason) or "타점 불명확")

    # 3) 목표가까지 상승여력 (15)
    w = weights.get("upside_room", 15)
    target = note.target1 or note.target_mean or tech.high_20
    upside = (target - price) / price * 100 if target and price else None
    if upside is None:
        s = w * 0.3
        txt = "목표가 없음"
    else:
        s = _clamp(upside / 15 * w, 0, w)  # 15% 여력에서 만점
        txt = f"상승여력 {upside:+.1f}%"
    b.add("상승여력", s, w, txt)

    # 4) 거래량/거래대금 증가 (20)
    w = weights.get("volume_surge", 20)
    surge = tech.volume_surge
    s = 0.0
    reason = []
    if surge:
        # 정상 유동성(>=0.8배)은 기본 0.4w + 급증분 가점(2배에서 만점). 거래 위축은 감점.
        s = _clamp((0.4 + max(surge - 1.0, 0.0) * 0.6) * w, 0, w) if surge >= 0.8 else surge * 0.4 * w
        reason.append(f"거래량 5/20일 {surge:.2f}배")
    if tech.trading_value_eok is not None and tech.trading_value_eok < min_trading_value_eok:
        s = min(s, w * 0.2)
        reason.append(f"거래대금 {tech.trading_value_eok}억<{min_trading_value_eok}억(주의)")
    b.add("거래량/거래대금", s, w, ", ".join(reason) or "변동 적음")

    # 5) 거시/이벤트 리스크 (10) — 위험 낮을수록 高
    w = weights.get("macro_event_risk", 10)
    risk_rank = {RiskLevel.LOW: 1.0, RiskLevel.MEDIUM: 0.7, RiskLevel.HIGH: 0.0}
    s = w * min(risk_rank[macro_risk], risk_rank[event_risk])
    b.add("거시/이벤트 안전도", s, w, f"거시 {macro_risk.value}·이벤트 {event_risk.value}")

    # 6) 손절 짧게 가능한 구조 (10) — 효과적 손절거리(노트 손절가 없으면 -3% 가정)
    w = weights.get("short_stop_structure", 10)
    eff_stop = note.stop if (note.stop and price and note.stop < price) else (price * 0.97 if price else None)
    if eff_stop and price:
        down = (eff_stop - price) / price * 100  # 음수
        # -3% 근처면 이상적(만점). 깊을수록 감점하되 최저 0.4w.
        s = _clamp(w * (1 - max(abs(down) - 3, 0) / 4), w * 0.4, w)
        txt = f"손절까지 {down:+.1f}%" + ("" if note.stop else "(% 기반 -3%)")
    else:
        s = w * 0.3
        txt = "손절가 노트에 없음(% 기반 계산 예정)"
    b.add("손절 구조", s, w, txt)

    return b
