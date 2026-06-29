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
