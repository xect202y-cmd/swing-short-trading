"""플랜 빌더 — 시나리오 가중/상한·qty 산정·직렬화 왕복."""
from swing_trader.scalp.planner import build_plan, load_plans, save_plan
from swing_trader.scalp.strategy import PlanItem

M3 = ("v1", "v2", "v3")   # 멀티모델 계획 검증(라이브 기본은 v3 단일)


def cand(t, name, tv=100.0, uptrend=True, prev_close=10000.0):
    return {"ticker": t, "name": name, "prev_close": prev_close,
            "prev_range": 300.0, "prev_tv_eok": tv, "uptrend": uptrend}


def _scen(risk="낮음", focus=""):
    return {"risk": risk, "notes": [], "focus_text": focus}


def test_v1_caps_at_5_and_sorts_by_trading_value():
    cands = [cand(f"00000{i}", f"종목{i}", tv=float(i)) for i in range(1, 8)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000, "v3": 3_000_000}, _scen(), quotes={}, models=M3)
    assert len(plan["v1"]) == 5
    assert plan["v1"][0].ticker == "000007"   # 거래대금 최대 우선


def test_high_risk_caps_v1_at_2_but_shadow_keeps_5():
    cands = [cand(f"00000{i}", f"종목{i}", tv=float(i)) for i in range(1, 8)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000, "v3": 3_000_000}, _scen(risk="높음"), quotes={}, models=M3)
    assert len(plan["v1"]) == 2
    assert len(plan["v1_shadow"]) == 5
    assert all(it.shadow for it in plan["v1_shadow"])


def test_focus_name_boosts_to_front():
    cands = [cand("000001", "삼성전자", tv=1.0), cand("000002", "포스코", tv=99.0)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000, "v3": 3_000_000},
                      _scen(focus="오늘은 삼성전자 반도체 모멘텀 주목"), quotes={}, models=M3)
    assert plan["v1"][0].name == "삼성전자"


def test_v2_requires_uptrend():
    cands = [cand("000001", "A", uptrend=False), cand("000002", "B", uptrend=True)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000, "v3": 3_000_000}, _scen(), quotes={}, models=M3)
    assert [i.ticker for i in plan["v2"]] == ["000002"]


def test_qty_from_budget_and_quote_price():
    cands = [cand("000001", "A", prev_close=100_000.0)]
    plan = build_plan(cands, {"v1": 3_000_000, "v2": 3_000_000, "v3": 3_000_000}, _scen(),
                      quotes={"000001": 120_000.0}, models=M3)   # 실시간가 우선
    assert plan["v1"][0].qty == 5   # (3_000_000/5)//120_000


def test_plan_save_load_roundtrip(tmp_path):
    items = [PlanItem(model="v1", ticker="000001", name="A", qty=3, stop_pct=-2.0,
                      prev_close=100.0, prev_range=5.0, k=0.5)]
    save_plan(tmp_path, "kr", {"date": "2026-07-03",
                               "scenario": {"risk": "낮음", "notes": [], "focus_text": ""},
                               "items": items})
    plans = load_plans(tmp_path)
    assert plans["kr"]["date"] == "2026-07-03"
    assert plans["kr"]["items"][0].ticker == "000001"   # PlanItem 으로 복원


def test_v3_requires_uptrend_and_v3_ok():
    ok = dict(cand("000001", "리턴구간"), v3_ok=True)
    no_flag = dict(cand("000002", "지지이탈"), v3_ok=False)
    down = dict(cand("000003", "역배열", uptrend=False), v3_ok=True)
    plan = build_plan([ok, no_flag, down], {"v1": 3_000_000, "v2": 3_000_000, "v3": 3_000_000},
                      _scen(), quotes={})
    assert [i.ticker for i in plan["v3"]] == ["000001"]
    it = plan["v3"][0]
    assert it.stop_pct == -1.0 and it.target_pct == 7.0   # 손익비 원칙 RR7


def test_v4_requires_uptrend_and_v4_ok():
    ok = dict(cand("000001", "급등후눌림"), v4_ok=True)
    no_flag = dict(cand("000002", "급등없음"), v4_ok=False)
    down = dict(cand("000003", "역배열", uptrend=False), v4_ok=True)
    plan = build_plan([ok, no_flag, down], {"v1": 3_000_000, "v2": 3_000_000, "v3": 3_000_000, "v4": 3_000_000},
                      _scen(), quotes={}, models=("v4",))
    assert [i.ticker for i in plan["v4"]] == ["000001"]
    it = plan["v4"][0]
    assert it.stop_pct == -1.5 and it.target_pct == 10.0   # v4 손익비
