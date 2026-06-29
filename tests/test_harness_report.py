from swing_trader.strategy import harness as H


def _t(entry, ret):
    return H.Trade(ticker="X", entry=entry, ret=ret)


def test_report_basic_metrics():
    trades = [_t("2026-01-05", 0.05), _t("2026-02-05", -0.03),
              _t("2026-03-05", 0.05), _t("2026-04-05", -0.03)]
    r = H.report_from_trades(trades)
    assert r.n_trades == 4
    assert r.win_rate == 50.0
    assert r.expectancy == round((0.05 - 0.03 + 0.05 - 0.03) / 4 * 100, 3)  # +1.0%
    assert r.profit_factor == round((0.05 + 0.05) / (0.03 + 0.03), 2)       # 1.67
    assert r.max_drawdown is not None and r.max_drawdown <= 0
    assert r.start == "2026-01-05" and r.end == "2026-04-05"
    assert r.trades_per_year is not None and r.trades_per_year > 0


def test_report_empty_is_safe():
    r = H.report_from_trades([])
    assert r.n_trades == 0 and r.expectancy is None and r.max_drawdown is None


def test_split_oos_by_date():
    trades = [H.Trade("X", f"2026-{m:02d}-05", 0.01) for m in range(1, 11)]  # 1~10월
    is_, oos = H.split_oos(trades, frac=0.3)
    assert is_ and oos
    # 분할 경계는 날짜 스팬 기준 — OOS 첫 거래가 IS 마지막보다 늦다
    assert is_[-1].entry < oos[0].entry
    # 전체 보존
    assert len(is_) + len(oos) == 10


def test_split_oos_empty():
    assert H.split_oos([], 0.3) == ([], [])


def test_judge_improve_and_worse():
    base = H.BacktestReport(n_trades=200, expectancy=0.50, sharpe=0.10)
    better = H.BacktestReport(n_trades=200, expectancy=0.80, sharpe=0.20)
    worse = H.BacktestReport(n_trades=200, expectancy=0.30, sharpe=0.02)
    same = H.BacktestReport(n_trades=200, expectancy=0.505, sharpe=0.101)
    assert H._judge(base, better) == "improve"
    assert H._judge(base, worse) == "worse"
    assert H._judge(base, same) == "neutral"


def test_compare_sample_guard(monkeypatch):
    # 거래 수가 적으면 insufficient. simulate_trades를 가짜로 대체.
    few = [H.Trade("X", "2026-01-05", 0.01), H.Trade("X", "2026-06-05", -0.01)]
    monkeypatch.setattr(H, "simulate_trades", lambda *a, **k: few)
    ab = H.compare(cfg=None, provider=None, notes=[], days=500,
                   baseline={}, candidate={"runner": True}, oos_fraction=0.3, min_oos=100)
    assert ab.verdict == "insufficient" and ab.sample_ok is False
