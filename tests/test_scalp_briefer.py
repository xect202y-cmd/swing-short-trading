"""단타 브리핑 — 스윙과 구분되는 임베드(오렌지·⚡)와 md 렌더."""
from swing_trader.scalp.account import ScalpState
from swing_trader.scalp.briefer import ORANGE, scalp_brief
from swing_trader.scalp.strategy import PlanItem


def test_brief_contains_results_and_plan(tmp_path):
    state = ScalpState.load(tmp_path)
    settled = {"v1": [{"name": "삼성전자", "entry": 61000.0, "exit": 61600.0,
                       "pnl": 5900.0, "ret_pct": 0.98, "reason": "종가청산"}], "v2": []}
    plan = {"date": "2026-07-03",
            "scenario": {"risk": "낮음", "notes": ["VIX 15 안정"], "focus_text": ""},
            "items": [PlanItem(model="v1", ticker="005930", name="삼성전자", qty=9,
                               stop_pct=-2.0, prev_close=61000.0, prev_range=800.0,
                               k=0.5, trigger=61400.0, why="거래대금 상위")]}
    embed, md = scalp_brief("kr", settled, plan, state, "2026-07-02")
    assert embed["title"].startswith("⚡ 단타 페이퍼 · KR")
    assert embed["color"] == ORANGE
    joined = str(embed) + md
    assert "삼성전자" in joined and "61,400" in joined   # 계획 트리거가 노출
    assert "+5,900" in joined or "5,900" in joined       # 결과 손익 노출
    assert "300만" in str(embed["footer"])


def test_brief_no_results_says_so(tmp_path):
    state = ScalpState.load(tmp_path)
    plan = {"date": "2026-07-03", "scenario": {"risk": "낮음", "notes": [], "focus_text": ""},
            "items": []}
    embed, md = scalp_brief("us", {"v1": [], "v2": []}, plan, state, "2026-07-02")
    assert "체결 없음" in str(embed) + md
