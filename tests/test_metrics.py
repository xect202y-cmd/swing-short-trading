from swing_trader.strategy.metrics import TradeRec, full_report


def _mk():
    # 6 trades across 2 regimes; alternating win/loss with a 3-loss streak in middle
    data = [
        ("2025-01-06", 0.05, "BULL", 3),
        ("2025-01-20", 0.06, "BULL", 4),
        ("2025-02-10", -0.02, "BEAR", 2),
        ("2025-02-24", -0.025, "BEAR", 2),
        ("2025-03-10", -0.03, "BEAR", 5),
        ("2025-03-24", 0.04, "BULL", 3),
    ]
    return [TradeRec(*d) for d in data]


def test_core_metrics():
    r = full_report(_mk(), fixed_frac=0.2)
    assert r["n_trades"] == 6
    assert r["win_rate"] == round(3 / 6 * 100, 1)
    assert r["avg_hold_days"] == round((3 + 4 + 2 + 2 + 5 + 3) / 6, 1)
    assert r["max_consec_losses"] == 3
    assert r["profit_factor"] > 1.0
    assert r["realized_rr"] > 0
    assert set(r["by_regime"]) == {"BULL", "BEAR"}
    assert r["by_regime"]["BEAR"]["n"] == 3


def test_regime_sizing_reduces_mdd():
    trades = _mk()
    flat = full_report(trades, frac_by_regime={"BULL": 0.4, "NEUTRAL": 0.4, "BEAR": 0.4, "CRASH": 0.4})
    downsized = full_report(trades, frac_by_regime={"BULL": 0.4, "NEUTRAL": 0.3, "BEAR": 0.2, "CRASH": 0.1})
    # smaller bear sizing → shallower drawdown (less negative)
    assert downsized["mdd_pct"] >= flat["mdd_pct"]
    assert "sortino" in downsized and "calmar" in downsized
