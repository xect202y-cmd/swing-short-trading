"""일/주 실현손익 윈도우 회귀 테스트.

버그(2026-07-04): run_once 가 OrderManager 에 realized_today 로 broker.realized_pnl
(전체 누적 실현손익)을 넘겨, 오래전 손실 때문에 '1일 손실 한도'가 영구히 걸려
스윙 봇이 매일 신규매수 0으로 막힘. 일/주 한도는 '그 날/그 주' 실현손익만 봐야 한다.
"""
import json

from swing_trader.review import analytics as A


def _write_closed(state_dir, rows):
    (state_dir / "closed_trades.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_realized_since_only_counts_window(tmp_path):
    _write_closed(tmp_path, [
        {"ticker": "AVGO", "pnl": -300000, "exit_date": "2026-06-23"},  # 오래전 큰 손실
        {"ticker": "TSMC", "pnl": 5000, "exit_date": "2026-07-04"},     # 오늘 소폭 이익
    ])
    # 오늘(7/4) 기준: 오래전 -300000 은 제외, 오늘 +5000 만.
    assert A.realized_since(tmp_path, "2026-07-04") == 5000
    # 이번 주(월 6/29~) 기준: 오늘 것만(오래전 6/23 은 지난주라 제외).
    assert A.realized_since(tmp_path, "2026-06-29") == 5000
    # 아주 옛날 기준: 전부 합산.
    assert A.realized_since(tmp_path, "2000-01-01") == -295000


def test_realized_since_includes_unpersisted_exits(tmp_path):
    _write_closed(tmp_path, [{"ticker": "OLD", "pnl": -300000, "exit_date": "2026-06-23"}])
    # 이번 런에서 막 청산됐지만 아직 파일에 안 들어간 거래(extra).
    extra = [{"ticker": "NEW", "pnl": -160000, "exit_date": "2026-07-04"}]
    assert A.realized_since(tmp_path, "2026-07-04", extra) == -160000


def test_realized_since_empty(tmp_path):
    assert A.realized_since(tmp_path, "2026-07-04") == 0.0
