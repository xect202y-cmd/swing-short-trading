"""v10 라이브 — 계정 재조정 + 신호 빌더 + 라이브 루프."""
import json
from pathlib import Path

from swing_trader.broker.paper import PaperBroker


def test_paper_account_reconciled_flat():
    # A0: 재조정 후 브로커는 flat(보유 0), 현금은 대시보드 스냅샷과 일치.
    root = Path(__file__).resolve().parents[1]
    broker = PaperBroker(seed_cash=5_000_000, state_path=root / "state" / "paper_state.json")
    o = json.loads((root / "state" / "open_positions.json").read_text(encoding="utf-8"))
    assert broker.get_positions() == []
    assert broker.get_cash_balance() == round(float(o["cash"]), 2)
