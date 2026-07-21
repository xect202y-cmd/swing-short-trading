"""가짜(synthetic) 시세 매매 차단 회귀 테스트.

2026-07-20: pykrx/yfinance 둘 다 실패 → 전 종목 synthetic 폴백인데도 run_once 가
페이퍼 매수를 강행해 삼양식품 등을 가짜가(69,509원, 실제 ~105만원)에 매수한 사고.
run_once 는 (1) 전부 synthetic 이면 매매 사이클 전체 스킵 (2) 일부만 synthetic 이면
그 종목만 매수 제외해야 한다 — 둘 다 회귀 방지 테스트(네트워크 없이 provider 를 fake 로 주입).
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from swing_trader import main as m
from swing_trader.config import Config, Credentials, Safety
from swing_trader.market import fx as _FX
from swing_trader.models import RiskLevel, Signal, SignalKind, StockNote, TradePlan


def _ohlcv_df(price: float, days: int = 5) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    return pd.DataFrame(
        {"open": [price] * days, "high": [price * 1.01] * days, "low": [price * 0.99] * days,
         "close": [price] * days, "volume": [1_000_000] * days}, index=idx,
    )


class _FakeProvider:
    """provider.get_ohlcv 를 미리 정한 (가격, source) 로만 응답 — 네트워크 없음."""

    def __init__(self, presets: dict[str, tuple[float, str]]):
        self._presets = presets
        self.sources: dict[str, str] = {}

    def get_ohlcv(self, ticker):
        price, source = self._presets[ticker]
        self.sources[ticker] = source
        return _ohlcv_df(price), source


class _FakeEngine:
    """engine.scan 이 실제로 provider.get_ohlcv 를 호출해 sources 를 채우는 것까지 흉내."""

    def __init__(self, provider: _FakeProvider, signals: list[Signal]):
        self._provider = provider
        self._signals = signals
        self.regime = None

    def scan(self, notes, macro, events):
        for n in notes:
            self._provider.get_ohlcv(n.ticker)
        return self._signals


class _FakeWriter:
    def __init__(self, tmp_path):
        self._dir = tmp_path

    def write_signals(self, signals):
        p = self._dir / "signals.md"
        p.write_text("signals", encoding="utf-8")
        return p

    def write_daily(self, md):
        pass

    def append_trades(self, orders):
        p = self._dir / "trades.md"
        p.write_text("trades", encoding="utf-8")
        return p


class _FakeReader:
    def __init__(self, notes):
        self._notes = notes

    def stock_notes(self, limit=None):
        return self._notes


def _note(ticker: str, name: str) -> StockNote:
    return StockNote(name=name, ticker=ticker)


def _plan() -> TradePlan:
    return TradePlan(entry=10000.0, stop=9700.0, target1=10600.0, target2=10850.0)


def _buy_signal(ticker: str, name: str) -> Signal:
    return Signal(ticker=ticker, name=name, kind=SignalKind.BUY, score=80.0, price=10000.0,
                  plan=_plan(), event_risk=RiskLevel.LOW)


def _cfg(tmp_path, **capital_overrides) -> Config:
    raw = {
        "capital": {
            "seed": 1_000_000, "max_positions": 5, "first_entry": 300000, "max_per_stock": 500000,
            "max_total_exposure_pct": 100, "max_per_stock_pct": 100, "max_sector_pct": 100,
            "max_new_per_day": 5, **capital_overrides,
        },
        "risk": {"daily_loss_limit": -30000, "account_loss_limit": -50000, "min_reward_risk": 1.5},
        "paper": {"fee_bps": 1.5, "slippage_bps": 5.0},
    }
    return Config(raw=raw, vault_root=tmp_path, safety=Safety(), creds=Credentials(), state_dir=tmp_path)


@pytest.fixture(autouse=True)
def _fx_cache_no_network(monkeypatch):
    monkeypatch.setitem(_FX._cache, "rate", 1400.0)  # get_usdkrw 네트워크 회피


def test_all_synthetic_skips_entire_trade_cycle(tmp_path, monkeypatch):
    """전부 synthetic → 매매 사이클 전체 스킵(청산/매수 모두 미실행), record_done 도 안 남김."""
    cfg = _cfg(tmp_path)
    notes = [_note("A1", "가짜종목1"), _note("A2", "가짜종목2")]
    provider = _FakeProvider({"A1": (69509.0, "synthetic"), "A2": (50000.0, "synthetic")})
    signals = [_buy_signal("A1", "가짜종목1")]
    engine = _FakeEngine(provider, signals)
    writer = _FakeWriter(tmp_path)
    reader = _FakeReader(notes)

    monkeypatch.setattr(m, "_build", lambda cfg: (reader, provider, None, None, engine, writer))
    monkeypatch.setattr(
        m, "PaperBroker",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("데이터 불건전인데 브로커가 생성됨(매매 시도)")))
    monkeypatch.setattr(
        m._DM, "record_done",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("데이터 불건전인데 record_done 호출됨")))

    result = m.run_once(cfg, market="all")

    assert result["data_unhealthy"] is True
    assert result["bought"] == 0 and result["sold"] == 0 and result["blocked"] == []
    assert result["cash"] is None and result["realized"] is None and result["trades"] is None
    assert not (tmp_path / "paper_state.json").exists()


def test_partial_synthetic_excludes_only_that_ticker(tmp_path, monkeypatch):
    """일부만 synthetic → 그 종목만 매수 제외, 실데이터 종목은 정상 매수."""
    cfg = _cfg(tmp_path)
    notes = [_note("REAL1", "리얼종목"), _note("FAKE1", "가짜종목")]
    provider = _FakeProvider({"REAL1": (10000.0, "pykrx"), "FAKE1": (10000.0, "synthetic")})
    sig_real = _buy_signal("REAL1", "리얼종목")
    sig_fake = _buy_signal("FAKE1", "가짜종목")
    signals = [sig_real, sig_fake]
    engine = _FakeEngine(provider, signals)
    writer = _FakeWriter(tmp_path)
    reader = _FakeReader(notes)

    monkeypatch.setattr(m, "_build", lambda cfg: (reader, provider, None, None, engine, writer))

    result = m.run_once(cfg, market="all")

    assert result.get("data_unhealthy") is None
    assert result["bought"] == 1

    state = json.loads((tmp_path / "paper_state.json").read_text(encoding="utf-8"))
    assert "REAL1" in state["positions"]
    assert "FAKE1" not in state["positions"]

    # synthetic 종목 신호는 BUY_WATCH 로 강등되고 사유가 남는다(장부에 매수되지 않음).
    assert sig_fake.kind == SignalKind.BUY_WATCH
    assert any("synthetic" in r for r in sig_fake.blocked_reasons)
    # 실데이터 종목 신호는 그대로 BUY 유지.
    assert sig_real.kind == SignalKind.BUY
