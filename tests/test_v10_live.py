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


def _second_candidate_panel(d: str):
    """같은 날 두 번째 거감짜름 후보(다른 티커) — sector 게이트 오탐 회귀 테스트용."""
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    closes = base + [120.0, 119.5]
    opens = list(closes[:260]) + [113.0, 120.0]
    vols = [1e6] * 260 + [3e6, 5e5]
    idx = pd.date_range("2024-01-02", periods=262, freq="B")
    df = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes),
                       "low": np.minimum(opens, closes), "close": closes,
                       "volume": vols}, index=idx)
    assert idx[-1].strftime("%Y-%m-%d") == d
    return df


def _live_cfg(tmp_path, seed: float = 5_000_000):
    """run_v10_live 최소 라이브 cfg — state_dir=tmp_path(빈 계정), v10/risk/capital/paper 키만."""
    from swing_trader.config import Config, Credentials, Safety
    raw = {
        "v10": dict(high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0, window=3,
                    vol_dry=0.7, body_max=0.03, supply_days=3, regime_gate=True, regime_ma=50),
        "risk": dict(default_stop_pct=-3.0, take1_pct=6.0, max_stop_pct=-5.0, max_hold_days=40,
                    daily_loss_limit=-30000, account_loss_limit=-50000, min_reward_risk=1.5),
        "capital": dict(seed=seed, max_positions=3, first_entry=300000, max_per_stock=500000,
                        max_total_exposure_pct=60, max_per_stock_pct=25, max_sector_pct=35,
                        max_new_per_day=3),
        "paper": dict(fee_bps=1.5, slippage_bps=5.0),
        "indicators": {},
    }
    return Config(raw=raw, vault_root=tmp_path, safety=Safety(), creds=Credentials(), state_dir=tmp_path)


def test_run_v10_live_idempotent_and_enters(tmp_path, monkeypatch):
    """멱등: 같은 d 두 번 실행 → 두 번째는 진입 0. 인수 보유 없고 오늘 후보 1 → 진입 1."""
    from swing_trader.market import fx as _FX
    monkeypatch.setitem(_FX._cache, "rate", 1400.0)   # briefer._positions_data 의 fx 조회 네트워크 회피

    df, d = _panel_with_todays_entry()
    from swing_trader.strategy import v10_live as VL
    # 패널/유니버스/지수/수급/브리핑/디스코드/옵시디언/마커를 모킹(네트워크 없음)
    monkeypatch.setattr(VL, "_load_panel", lambda cfg: ({"005930": df}, {"005930": "KOSPI"}, d))
    monkeypatch.setattr(VL, "_regime_updays", lambda cfg, ma: (None, None))
    monkeypatch.setattr(VL, "_supply_provider", lambda cfg: None)   # 페일오픈
    calls = {"discord": 0, "vault": 0}
    monkeypatch.setattr(VL, "_notify", lambda *a, **k: calls.__setitem__("discord", calls["discord"] + 1))
    monkeypatch.setattr(VL, "_write_vault", lambda *a, **k: calls.__setitem__("vault", calls["vault"] + 1))

    r1 = VL.run_v10_live(_live_cfg(tmp_path))
    assert r1["entered"] == 1 and r1["held"] == 1
    assert calls["discord"] == 1 and calls["vault"] == 1
    r2 = VL.run_v10_live(_live_cfg(tmp_path))     # 같은 d 재실행
    assert r2["entered"] == 0                     # 멱등
    assert r2["held"] == 1                        # 기존 보유는 유지


def test_run_v10_live_two_same_day_candidates_both_placed(tmp_path, monkeypatch):
    """섹터게이트 회귀 방지: sector=None→'기타' 단일버킷이면 2번째 진입(40%>35%)이 오탐 차단됐었다.
    ticker=sector 버킷화로 같은 날 후보 2개(각 20%)가 모두 통과해야 한다."""
    from swing_trader.market import fx as _FX
    monkeypatch.setitem(_FX._cache, "rate", 1400.0)

    df1, d = _panel_with_todays_entry()
    df2 = _second_candidate_panel(d)
    from swing_trader.strategy import v10_live as VL
    monkeypatch.setattr(VL, "_load_panel",
                        lambda cfg: ({"005930": df1, "000660": df2},
                                     {"005930": "KOSPI", "000660": "KOSPI"}, d))
    monkeypatch.setattr(VL, "_regime_updays", lambda cfg, ma: (None, None))
    monkeypatch.setattr(VL, "_supply_provider", lambda cfg: None)
    monkeypatch.setattr(VL, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(VL, "_write_vault", lambda *a, **k: None)

    # seed 1.5M: 종목당 first_entry 30만원 = 20%(<=25% 종목한도), 합산 60만원 = 40%(>35% 섹터한도).
    # sector=None(둘 다 '기타' 버킷)이면 2번째 진입이 섹터한도로 오탐 차단됐던 버그의 재현조건.
    r1 = VL.run_v10_live(_live_cfg(tmp_path, seed=1_500_000))
    assert r1["entered"] == 2 and r1["held"] == 2
