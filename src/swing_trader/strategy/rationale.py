"""진입 근거 + 목표/손절 산출방식 — '왜 샀는지'를 사람이 검토 가능한 구조로.

백테스트 분석을 위해 dict 로 반환(=신호에 저장 → decision_log/Signals.md 기록).
수급(외국인/기관)·섹터 강세지수는 일봉 OHLCV만으론 한계 → best-effort/'데이터 제한' 표기.
"""
from __future__ import annotations


def entry_rationale(note, tech, macro_risk, event_risk, methods: list[str], min_tv: float) -> dict:
    # 진입유형 — 기술적 상태 + 부합 기법
    types: list[str] = []
    if tech.breakout_prev_high:
        types.append("돌파")
    if tech.near_ma20 and tech.above_ma20:
        types.append("눌림")
    if tech.ma_aligned:
        types.append("추세추종")
    if tech.rsi is not None and tech.rsi < 40 and tech.above_ma20:
        types.append("반등")
    entry_type = "/".join(dict.fromkeys(types)) or "기타"

    price_conds = []
    if tech.above_ma5:
        price_conds.append("5일선 위")
    if tech.above_ma20:
        price_conds.append("20일선 위")
    if tech.breakout_prev_high:
        price_conds.append("전일고가 돌파")
    elif tech.near_ma20:
        price_conds.append("20일선 근처(지지)")

    surge = tech.volume_surge
    vol_cond = (f"5/20일 평균 {surge:.2f}배" + ("(증가)" if surge and surge >= 1.1 else "(보통)")) if surge else "데이터 부족"
    trend = "정배열(단>중>장)" if tech.ma_aligned else ("20일선 상회" if tech.above_ma20 else "추세 약함")
    market = f"거시 {macro_risk.value} · 이벤트 {event_risk.value}"
    mkt_name = "KOSPI/KOSDAQ" if (tech.ticker or "").split(".")[0].isdigit() else "US"

    # 제외조건(과열·갭·변동성·유동성) 통과 점검
    flags = []
    if tech.gap_pct is not None and abs(tech.gap_pct) >= 3:
        flags.append(f"갭 {tech.gap_pct:+.1f}%")
    if tech.rsi is not None and tech.rsi >= 75:
        flags.append(f"RSI {tech.rsi} 과열")
    if tech.atr_pct is not None and tech.atr_pct >= 6:
        flags.append(f"ATR {tech.atr_pct}% 변동성과다")
    if tech.trading_value_eok is not None and tech.trading_value_eok < min_tv:
        flags.append(f"거래대금 {tech.trading_value_eok}억<{min_tv}")
    exclusion = "통과" if not flags else "주의: " + ", ".join(flags)

    return {
        "entry_type": entry_type,
        "price_conditions": price_conds or ["조건 약함"],
        "volume_condition": vol_cond,
        "trend_condition": trend,
        "supply_condition": "데이터 제한(외국인/기관 수급 미연동)",
        "sector_state": note.sector or "미상",
        "market_state": f"{mkt_name} · {market}",
        "exclusion_pass": exclusion,
        "matched_methods": methods,
    }


def target_stop_methods(tech, note, take1_pct: float, stop_pct: float,
                        atr_target_mult: float = 2.0, atr_stop_mult: float = 1.5) -> dict:
    """고정비율/ATR/지지저항 3가지 목표·손절 후보를 모두 계산(비교·백테스트용)."""
    p = tech.price
    atr = tech.atr or p * 0.02
    res = max(tech.high_20 or p, tech.prev_high or p)
    sup = tech.low_20 or p * (1 + stop_pct / 100)

    out = {
        "고정비율": {
            "target": round(p * (1 + take1_pct / 100), 2),
            "stop": round(p * (1 + stop_pct / 100), 2),
            "note": f"매수가 대비 +{take1_pct:.1f}% / {stop_pct:.1f}%",
        },
        "ATR": {
            "target": round(p + atr_target_mult * atr, 2),
            "stop": round(p - atr_stop_mult * atr, 2),
            "note": f"+{atr_target_mult}ATR / -{atr_stop_mult}ATR (ATR {tech.atr_pct}%)",
        },
        "지지저항": {
            "target": round(res if res > p * 1.01 else p * (1 + take1_pct / 100), 2),
            "stop": round(sup if sup < p * 0.995 else p * (1 + stop_pct / 100), 2),
            "note": f"목표=20일고 {res:,.0f} / 손절=20일저 {sup:,.0f}",
        },
    }
    for m in out.values():
        risk = (p - m["stop"]) / p
        reward = (m["target"] - p) / p
        m["rr"] = round(reward / risk, 2) if risk > 0 else None
        m["atr_stop_mult"] = round((p - m["stop"]) / atr, 2) if atr else None
    return out
