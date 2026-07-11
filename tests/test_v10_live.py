"""v10 라이브 — 계정 재조정 + 신호 빌더 + 라이브 루프."""
import dataclasses
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from swing_trader.broker.paper import PaperBroker
from swing_trader.config import load_config
from swing_trader.models import SignalKind
from swing_trader.strategy import v10_live


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


def _panel_with_todays_entry():
    # 260봉 박스 + [돌파, 거감짜름(=오늘 d)] → 오늘 진입 후보 1건.
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    closes = base + [110.0, 109.5]
    opens = list(closes[:260]) + [105.0, 110.0]
    vols = [1e6] * 260 + [3e6, 5e5]
    idx = pd.date_range("2024-01-02", periods=262, freq="B")
    df = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes),
                       "low": np.minimum(opens, closes), "close": closes,
                       "volume": vols}, index=idx)
    return df, idx[-1].strftime("%Y-%m-%d")


class _Cfg:
    def get(self, *k, default=None):
        t = {("v10",): dict(high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0, window=3,
                            vol_dry=0.7, body_max=0.03, supply_days=3),
             ("risk", "default_stop_pct"): -3.0, ("risk", "take1_pct"): 6.0,
             ("risk", "max_hold_days"): 40}
        return t.get(tuple(k), default)


def test_build_v10_signals_todays_entry_only():
    df, d = _panel_with_todays_entry()
    sigs = v10_live.build_v10_signals(_Cfg(), {"005930": df}, d, supply=None,
                                      kospi_up=None, kosdaq_up=None, market_of={"005930": "KOSPI"})
    assert len(sigs) == 1
    s = sigs[0]
    assert s.ticker == "005930" and s.kind == SignalKind.BUY
    assert s.plan is not None and abs(s.plan.entry - 109.5) < 1e-6   # 거감짜름일 종가
    assert s.plan.stop < s.plan.entry < s.plan.target1


def test_build_v10_signals_supply_hardgate_off_in_live():
    # 라이브: 수급 None(데이터 없음) → 페일오픈으로 진입 신호 유지.
    df, d = _panel_with_todays_entry()
    sigs = v10_live.build_v10_signals(_Cfg(), {"005930": df}, d, supply=None,
                                      kospi_up=None, kosdaq_up=None, market_of={"005930": "KOSPI"})
    assert len(sigs) == 1
