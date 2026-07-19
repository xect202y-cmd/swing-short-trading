"""equity_history 방어 가드 + canonical 보유가치 함수 회귀 테스트 (P3-1).

배경(2026-07-07 가짜 폭락, equity==cash −39.99%): equity_history writer 2곳(daily_brief·
v10_live) 의 보유가치 공식이 비대칭(US 슬리브 누락)이었고, record_equity 는 무가드라
잘못된 holdings_value(0/NaN)도 그대로 곡선에 찍었다. 여기서는 (1) 두 writer가 공유할
단일 원천 briefer.holdings_value_krw 가 US 슬리브를 포함하는지, (2) record_equity 가
'보유 존재 & holdings_value 0/NaN'인 명백한 이상만 스킵하고 실제 큰 변동은 위조 없이
그대로 기록하는지를 검증한다.
"""
import json

from swing_trader.review import analytics as A
from swing_trader.review import briefer as B


def _rows(state_dir):
    return json.loads((state_dir / "equity_history.json").read_text(encoding="utf-8"))


def _write_us_state(state_dir, qty=5, cur_usd=210.0, mark_fx=1400.0):
    us_state = {"asOf": "2026-07-07", "realized_krw": 0.0,
                "open": [{"ticker": "AAPL", "entry_date": "2026-07-01", "entry_usd": 200.0,
                          "fx_entry": 1380.0, "qty": qty, "bars_held": 3,
                          "cur_usd": cur_usd, "mark_fx": mark_fx}],
                "trades": [], "equity_hist": []}
    (state_dir / "v1_us_state.json").write_text(json.dumps(us_state), encoding="utf-8")


# ── P3-1a: canonical holdings_value_krw(단일 원천) ──
def test_holdings_value_krw_includes_us_sleeve(tmp_path):
    _write_us_state(tmp_path)
    assert B.holdings_value_krw(tmp_path, 1_000_000) == 1_000_000 + 5 * 210.0 * 1400.0


def test_holdings_value_krw_no_us_state_returns_hv_only(tmp_path):
    assert B.holdings_value_krw(tmp_path, 1_000_000) == 1_000_000


# ── P3-1b: record_equity 방어 가드 ──
def test_record_equity_skips_zero_holdings_when_positions_exist(tmp_path):
    A.record_equity(tmp_path, "2026-07-06", 3_000_000, 2_000_000, 5_000_000, positions_exist=True)
    A.record_equity(tmp_path, "2026-07-07", 3_000_000, 0, 5_000_000, positions_exist=True)  # 보유 있는데 0 → 이상치
    rows = _rows(tmp_path)
    assert len(rows) == 1 and rows[0]["date"] == "2026-07-06"   # 07-07 행 미기록(직전 상태 유지)


def test_record_equity_skips_nan_holdings_when_positions_exist(tmp_path):
    A.record_equity(tmp_path, "2026-07-06", 3_000_000, 2_000_000, 5_000_000, positions_exist=True)
    A.record_equity(tmp_path, "2026-07-07", 3_000_000, float("nan"), 5_000_000, positions_exist=True)
    rows = _rows(tmp_path)
    assert len(rows) == 1 and rows[0]["date"] == "2026-07-06"


def test_record_equity_allows_zero_holdings_when_no_positions(tmp_path):
    # 보유가 실제로 없으면 holdings=0 은 정상 — 가드가 막으면 안 된다.
    A.record_equity(tmp_path, "2026-07-07", 5_000_000, 0, 5_000_000, positions_exist=False)
    rows = _rows(tmp_path)
    assert len(rows) == 1 and rows[0]["holdings"] == 0


def test_record_equity_allows_large_real_move_without_guard(tmp_path):
    # 실제 큰 하락은 위조 없이 그대로 기록돼야 한다(급변 임계 가드 금지 — holdings 자체는 정상값).
    A.record_equity(tmp_path, "2026-07-06", 1_000_000, 4_000_000, 5_000_000, positions_exist=True)
    A.record_equity(tmp_path, "2026-07-07", 1_000_000, 500_000, 5_000_000, positions_exist=True)
    rows = _rows(tmp_path)
    assert len(rows) == 2 and rows[-1]["holdings"] == 500_000


def test_record_equity_backward_compatible_without_positions_exist(tmp_path):
    # 기존 호출부(positions_exist 생략)는 가드 없이 그대로 동작(하위호환).
    A.record_equity(tmp_path, "2026-07-07", 3_000_000, 0, 5_000_000)
    rows = _rows(tmp_path)
    assert len(rows) == 1 and rows[0]["holdings"] == 0
