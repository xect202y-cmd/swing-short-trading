"""단타 체결 판정 — look-ahead 금지·손절 우선·미체결 케이스."""
import pytest

from swing_trader.scalp.strategy import PlanItem, settle_item, V1_K


def bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def v1(qty=10, prev_close=10000.0, prev_range=400.0):
    return PlanItem(model="v1", ticker="005930", name="삼성전자", qty=qty,
                    stop_pct=-2.0, prev_close=prev_close, prev_range=prev_range, k=V1_K)


def v2(qty=10, prev_close=10000.0):
    return PlanItem(model="v2", ticker="005930", name="삼성전자", qty=qty,
                    stop_pct=-2.5, prev_close=prev_close, prev_range=300.0)


def test_v1_no_fill_when_high_below_trigger():
    # trigger = 10000 + 0.5*400 = 10200 > high 10150 → 미체결
    assert settle_item(v1(), bar(10000, 10150, 9900, 10100), 1.5, 5.0) is None


def test_v1_fills_at_trigger_and_exits_at_close():
    f = settle_item(v1(), bar(10000, 10400, 10050, 10350), 1.5, 5.0)
    cost = (1.5 + 5.0) / 10000
    assert f.reason == "종가청산"
    assert f.entry == pytest.approx(10200 * (1 + cost))
    assert f.exit == pytest.approx(10350 * (1 - cost))
    assert f.pnl == pytest.approx((f.exit - f.entry) * 10)


def test_v1_trigger_anchors_on_today_open():
    # 트리거 = 당일 시가(10300) + 0.5*400 = 10500 > 고가 10450 → 미체결
    # (전일종가 앵커였다면 10000+200=10200 < 10450 → 체결됐을 것 — 앵커 선택을 검증)
    assert settle_item(v1(), bar(10300, 10450, 10250, 10400), 1.5, 5.0) is None


def test_v1_low_below_stop_still_exits_at_close():
    # v1은 장중 트리거 진입이라 당일 저가가 진입 '전'(아침 눌림)일 수 있음 —
    # 저가 기반 손절 판정은 승자를 손절로 오판(500일 실증: 기대값 -1.2%p 왜곡).
    # → v1 손절은 일봉으로 시뮬 불가, 종가 청산만 인정.
    f = settle_item(v1(), bar(10000, 10400, 9800, 10350), 1.5, 5.0)
    assert f.reason == "종가청산"
    cost = (1.5 + 5.0) / 10000
    assert f.exit == pytest.approx(10350 * (1 - cost))


def test_v2_stop_first_when_low_touches():
    # v2는 시가 진입 → 당일 저가는 항상 진입 이후 → 저가 기반 손절 판정 타당(유지)
    f = settle_item(v2(), bar(9750, 9950, 9200, 9900), 1.5, 5.0)   # -2.5% 갭 + 급락
    assert f.reason == "손절"
    cost = (1.5 + 5.0) / 10000
    entry = 9750 * (1 + cost)
    stop_price = entry * (1 - 0.025)
    assert f.exit == pytest.approx(stop_price * (1 - cost))


def test_v2_no_fill_without_gap_down():
    # 시가 9900 = -1% 갭 → -2% 미달 → 미체결
    assert settle_item(v2(), bar(9900, 10100, 9850, 10050), 1.5, 5.0) is None


def test_v2_fills_at_open_on_gap_down():
    f = settle_item(v2(), bar(9750, 9950, 9700, 9900), 1.5, 5.0)   # -2.5% 갭
    assert f.reason == "종가청산"
    assert f.entry == pytest.approx(9750 * (1 + (1.5 + 5.0) / 10000))


def test_serialization_roundtrip():
    from dataclasses import asdict
    d = asdict(v1())
    assert d["model"] == "v1" and d["k"] == V1_K
    assert PlanItem(**d) == v1()
