"""v10 라이브 — 계정 재조정 + 신호 빌더 + 라이브 루프."""
import dataclasses
import json
from datetime import date
from pathlib import Path

from swing_trader.broker.paper import PaperBroker
from swing_trader.config import load_config


def test_paper_account_reconciled_flat():
    # A0: 재조정 후 브로커는 flat(보유 0), 현금은 대시보드 스냅샷과 일치.
    root = Path(__file__).resolve().parents[1]
    broker = PaperBroker(seed_cash=5_000_000, state_path=root / "state" / "paper_state.json")
    o = json.loads((root / "state" / "open_positions.json").read_text(encoding="utf-8"))
    assert broker.get_positions() == []
    assert broker.get_cash_balance() == round(float(o["cash"]), 2)


def test_v10_config_live_knobs():
    cfg = load_config()
    assert cfg.get("regime", "adopted_version") == "v10"
    assert isinstance(cfg.get("v10", "alloc_pct"), (int, float))
    assert cfg.get("v10", "rank") in ("momentum", "newhigh_strength")


def test_append_swing_v10_writes_to_signals(tmp_path):
    from swing_trader.obsidian.writer import VaultWriter

    cfg = load_config()
    cfg = dataclasses.replace(cfg, vault_root=tmp_path)  # 실제 볼트 오염 방지(Config는 frozen dataclass)
    w = VaultWriter(cfg)
    p = w.append_swing_v10("### 테스트 v10\n> 내용\n", d=date(2026, 7, 11))
    assert p.exists() and "SwingV10" in p.name and "테스트 v10" in p.read_text(encoding="utf-8")
    assert p.is_relative_to(tmp_path)
