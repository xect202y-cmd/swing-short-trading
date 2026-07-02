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


def test_v1_stop_first_when_low_touches():
    # 저가가 손절가 이하 → 보수적으로 손절 체결(같은 날 고저 순서 모름)
    f = settle_item(v1(), bar(10000, 10400, 9800, 10350), 1.5, 5.0)
    assert f.reason == "손절"
    assert f.exit < f.entry
    cost = (1.5 + 5.0) / 10000
    entry = 10200 * (1 + cost)
    stop_price = entry * (1 - 0.02)
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
