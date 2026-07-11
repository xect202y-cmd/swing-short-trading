"""매수/매도 조건 룰 — 점수+기술적 상태 기반. 사람이 읽을 사유를 함께 반환."""
from __future__ import annotations

from ..models import Position, RiskLevel, StockNote, TechnicalSnapshot


def buy_reasons(note: StockNote, tech: TechnicalSnapshot) -> list[str]:
    r: list[str] = []
    if tech.near_ma20:
        r.append("20일선 근처 반등 구간")
    if tech.breakout_prev_high and tech.volume_surge and tech.volume_surge >= 1.2:
        r.append("전일고가 돌파 + 거래량 증가")
    target = note.target1 or note.target_mean or tech.high_20
    if target and (target - tech.price) / tech.price * 100 >= 5:
        r.append("목표가까지 상승여력 5%+")
    if note.buy_zone1 and tech.price <= note.buy_zone1:
        r.append("매수구간1 이하")
    return r


def buy_blocks(
    note: StockNote,
    tech: TechnicalSnapshot,
    macro_risk: RiskLevel,
    event_risk: RiskLevel,
    min_trading_value_eok: float,
    require_uptrend: bool = False,
    momentum_min_pct: float = 0.0,
) -> list[str]:
    """매수 차단 사유(있으면 BLOCKED)."""
    b: list[str] = []
    if require_uptrend:
        # Phase3 진입 필터: 종가>60일선 AND 20일선>60일선(상승배열)에서만. 백테스트와 동일 정의.
        ma20v, ma60v = tech.ma.get(20), tech.ma.get(60)
        if ma20v and ma60v and not (tech.price > ma60v and ma20v > ma60v):
            b.append("추세 미충족(60일선 하회 또는 역배열) — 상승배열에서만 진입")
    if momentum_min_pct > 0:
        # v9 진입 품질필터: 종가가 60일선 대비 momentum_min_pct%↑ 강세일 때만(약한 진입 제거).
        # 검증(2026-07-11): +5%↑ 시 승률 31→34%·PF 1.79→1.88·MDD −29→−23%·기대값 개선.
        ma60v = tech.ma.get(60)
        if ma60v and (tech.price / ma60v - 1) * 100 < momentum_min_pct:
            b.append(f"모멘텀 미충족(60일선 대비 +{momentum_min_pct:.0f}% 미만) — 강세 확인 후 진입(v9)")
    if event_risk == RiskLevel.HIGH:
        b.append("대형 이벤트 임박(D-0~1) — 신규매수 제한")
    if macro_risk == RiskLevel.HIGH:
        b.append("거시 리스크 높음 — 신규매수 제한")
    if tech.trading_value_eok is not None and tech.trading_value_eok < min_trading_value_eok:
        b.append(f"거래대금 {tech.trading_value_eok}억 < {min_trading_value_eok}억 — 유동성 부족")
    if (note.risk or "") and any(k in note.risk for k in ["거래정지", "소송", "상장폐지", "감사의견", "횡령"]):
        b.append(f"개별 악재 감지: {note.risk}")
    if (note.recommendable or "").strip() in {"불가", "no", "No", "추천불가"}:
        b.append("노트 추천가능여부=불가")
    return b


def sell_reasons(
    pos: Position,
    tech: TechnicalSnapshot,
    *,
    rsi_overbought: float,
    max_bars: int = 5,
    soft_hold_days: int = 10,
    max_hold_days: int = 20,
) -> list[str]:
    r: list[str] = []
    if tech.price <= pos.stop:
        r.append("손절가 도달")
    if tech.price >= pos.target1:
        r.append("1차 익절가 도달")
    if tech.rsi is not None and tech.rsi >= rsi_overbought and (tech.volume_surge or 1) < 1.0:
        r.append(f"RSI {tech.rsi} 과열 + 거래량 둔화")
    if pos.bars_held > max_bars:
        ma5 = tech.ma.get(5)
        if ma5 and tech.price < ma5:
            r.append(f"보유 {pos.bars_held}일 초과 + 모멘텀 약화")
    # 스윙 보유기간 규율 — 장기보유 금지
    if pos.bars_held >= max_hold_days:
        r.append(f"최대 보유 {max_hold_days}거래일(~{max_hold_days // 5}주) 도달 — 스윙 강제청산")
    elif pos.bars_held >= soft_hold_days and pos.avg_price and tech.price <= pos.avg_price:
        r.append(f"{soft_hold_days}거래일 경과 + 수익 없음 — 스윙 정리(자금 회전)")
    return r


def decide_exit(
    pos: Position,
    cur: float,
    tech: TechnicalSnapshot,
    *,
    rsi_overbought: float = 70,
    momentum_days: int = 5,
    soft_hold_days: int = 10,
    max_hold_days: int = 20,
    partial_pct: float = 0.5,
    trail_pct: float = 3.0,
    mode: str = "v5",
    vol_spike: float = 2.5,
) -> dict:
    """승자를 달리게 — 1차 익절서 절반 익절, 잔량은 트레일링으로 2차까지.

    mode="v7"이면 추세추종 청산: 익절 고정 없이 5일선 이탈/대량거래량 음봉까지 홀딩,
    손절 도달 시 청산(유튜브 마스터스윙 3편 수렴 — '빨리 팔지 말고 추세 살아있으면 홀딩').

    반환: {action: all|partial|hold, qty, reasons, hw, new_stop}
    """
    hw = max(pos.high_water or pos.avg_price, cur)
    # 부분익절 후엔 트레일링 스탑(최고가 대비) + 본전(브레이크이븐) 하한
    eff_stop = pos.stop
    if pos.partial_done:
        eff_stop = max(pos.stop, hw * (1 - trail_pct / 100), pos.avg_price)

    def res(action, qty, reasons, new_stop):
        return {"action": action, "qty": qty, "reasons": reasons, "hw": hw, "new_stop": new_stop}

    # v7 추세추종 청산 — 익절 고정 없음(승자 달리게). 손절/5일선 이탈/대량거래량 음봉/최대보유만.
    if mode == "v7":
        if cur <= pos.stop:
            return res("all", pos.quantity, ["손절가 도달"], pos.stop)
        v20 = tech.vol_avg.get(20)
        spike = (tech.vol_today / v20) if (tech.vol_today and v20 and v20 > 0) else None
        down_candle = tech.today_open is not None and cur < tech.today_open
        if down_candle and spike is not None and spike >= vol_spike:
            return res("all", pos.quantity, [f"대량거래량 음봉(거래량 {spike:.1f}배) — 세력 이탈"], pos.stop)
        ma5 = tech.ma.get(5)
        if pos.bars_held >= 1 and ma5 and cur < ma5:      # 5일선 이탈 = 추세 종료(진입 다음날부터)
            return res("all", pos.quantity, ["5일선 이탈 — 추세 종료"], pos.stop)
        if pos.bars_held >= max_hold_days:
            return res("all", pos.quantity, [f"최대 보유 {max_hold_days}거래일 — 강제청산"], pos.stop)
        return res("hold", 0, [], pos.stop)               # 추세 지속 → 홀딩(익절 고정 없이)

    # 1) 손절/트레일링 (전량)
    if cur <= eff_stop:
        why = f"트레일링 스탑({eff_stop:,.0f})" if pos.partial_done else "손절가 도달"
        return res("all", pos.quantity, [why], eff_stop)
    # 2) 2차 익절 (전량)
    if pos.target2 and cur >= pos.target2:
        return res("all", pos.quantity, ["2차 익절가 도달 — 잔량 청산"], eff_stop)
    # 3) 과열/모멘텀/보유기간 (전량)
    full = []
    if tech.rsi is not None and tech.rsi >= rsi_overbought and (tech.volume_surge or 1) < 1.0:
        full.append(f"RSI {tech.rsi} 과열 + 거래량 둔화")
    if pos.bars_held >= max_hold_days:
        full.append(f"최대 보유 {max_hold_days}거래일(~{max_hold_days // 5}주) — 강제청산")
    elif pos.bars_held > momentum_days and tech.ma.get(5) and cur < tech.ma[5]:
        full.append(f"보유 {pos.bars_held}일 초과 + 모멘텀 약화")
    elif pos.bars_held >= soft_hold_days and cur <= pos.avg_price:
        full.append(f"{soft_hold_days}거래일 + 수익 없음 — 정리(자금 회전)")
    if full:
        return res("all", pos.quantity, full, eff_stop)
    # 4) 1차 익절 — 절반 익절 + 본전 스탑(잔량은 추세 보유)
    if cur >= pos.target1 and not pos.partial_done:
        qty = int(pos.quantity * partial_pct)
        if qty < 1 or qty >= pos.quantity:        # 1주뿐이면 부분 불가 → 전량 익절
            return res("all", pos.quantity, ["1차 익절가 도달(수량 1 — 전량 익절)"], eff_stop)
        return res("partial", qty, ["1차 익절가 도달 — 절반 익절·잔량 추세 보유"], max(pos.stop, pos.avg_price))
    return res("hold", 0, [], eff_stop)
