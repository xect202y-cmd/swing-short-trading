"""단타 가상계좌 — 라이브는 채택 모델 1개(300만), 정산 멱등·멀티모델 적재."""
from swing_trader.scalp.account import SEED_PER_MODEL, ScalpState

M3 = ("v1", "v2", "v3")   # 정산 메커니즘 검증용(라이브는 단일이지만 로직은 모델-무관)


def test_fresh_state_defaults_to_adopted_single_model(tmp_path):
    st = ScalpState.load(tmp_path)                 # 기본 = 채택 v3 단일 계좌
    assert st.model_names == ("v3",)
    assert st.models["v3"]["cash"] == SEED_PER_MODEL
    assert "v1" not in st.models and "v2" not in st.models
    assert st.daily == [] and st.trades == []


def test_apply_day_updates_cash_and_daily(tmp_path):
    st = ScalpState.load(tmp_path, M3)
    st.apply_day("2026-07-03", "kr", {
        "v1": {"pnl": 15000.0, "shadow_pnl": 12000.0,
               "trades": [{"ticker": "005930", "pnl": 15000.0}]},
        "v2": {"pnl": -8000.0, "shadow_pnl": -8000.0, "trades": []},
    })
    assert st.models["v1"]["cash"] == SEED_PER_MODEL + 15000.0
    assert st.models["v2"]["realized"] == -8000.0
    assert st.models["v1"]["shadow_realized"] == 12000.0
    assert st.daily[-1] == {"date": "2026-07-03", "market": "kr",
                            "v1_pnl": 15000.0, "v2_pnl": -8000.0, "v3_pnl": 0.0,
                            "v1_shadow": 12000.0, "v2_shadow": -8000.0, "v3_shadow": 0.0}
    assert st.trades[-1]["date"] == "2026-07-03" and st.trades[-1]["model"] == "v1"


def test_apply_same_day_market_is_idempotent(tmp_path):
    st = ScalpState.load(tmp_path, M3)
    day = {"v1": {"pnl": 1000.0, "shadow_pnl": 0.0, "trades": []},
           "v2": {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []}}
    st.apply_day("2026-07-03", "kr", day)
    st.apply_day("2026-07-03", "kr", day)   # 재실행(failover 중복) → 덮어쓰기
    assert st.models["v1"]["cash"] == SEED_PER_MODEL + 1000.0
    assert len(st.daily) == 1


def test_save_load_roundtrip(tmp_path):
    st = ScalpState.load(tmp_path, M3)
    st.apply_day("2026-07-03", "us", {
        "v1": {"pnl": 500.0, "shadow_pnl": 500.0, "trades": []},
        "v2": {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []}})
    st.save(tmp_path)
    again = ScalpState.load(tmp_path, M3)
    assert again.models["v1"]["cash"] == SEED_PER_MODEL + 500.0
    assert again.daily == st.daily
