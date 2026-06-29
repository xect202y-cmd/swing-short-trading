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


def test_report_mdd_is_flat_bet_not_compounding():
    # flat-bet(가산)과 복리(곱)가 값이 달라지는 시퀀스로 곡선종류를 고정.
    # 가산: cum 1.0,2.0,1.5,1.0 → peak 2.0 → MDD = 1.0-2.0 = -1.0 → -100%
    # (복리였다면 -75%가 나옴)
    trades = [H.Trade("X", f"2026-{m:02d}-15", r)
              for m, r in [(1, 1.0), (2, 1.0), (3, -0.5), (4, -0.5)]]
    assert H.report_from_trades(trades).max_drawdown == -100.0


def test_compare_happy_path_improve(monkeypatch):
    # 날짜를 2년에 고루 분포시켜 OOS(뒤30%)에 100건 이상 들어가게 한다.
    def mk(n, ret):
        out = []
        for i in range(n):
            mo = 1 + (i % 24)               # 1..24 → 2024-01..2025-12
            y, m = 2024 + (mo - 1) // 12, (mo - 1) % 12 + 1
            out.append(H.Trade("X", f"{y}-{m:02d}-15", ret))
        return out
    base = mk(600, 0.004)                   # 기대값 +0.4%
    cand = mk(600, 0.010)                   # 기대값 +1.0% (> base + 0.02%p)
    monkeypatch.setattr(H, "simulate_trades",
                        lambda cfg, prov, notes, days, **p: cand if p.get("runner") else base)
    ab = H.compare(cfg=None, provider=None, notes=[], days=500,
                   baseline={}, candidate={"runner": True}, oos_fraction=0.3, min_oos=100)
    assert ab.sample_ok is True
    assert ab.n_oos >= 100
    assert ab.verdict == "improve"


def test_render_report_md_has_both_windows():
    is_rep = H.report_from_trades([H.Trade("X", "2026-01-05", 0.05), H.Trade("X", "2026-02-05", -0.03)])
    oos_rep = H.report_from_trades([H.Trade("X", "2026-05-05", 0.05), H.Trade("X", "2026-06-05", -0.03)])
    md = H.render_report_md("기준 로직 측정", is_rep, oos_rep, "2026-06-30")
    assert "인샘플" in md and "아웃오브샘플" in md
    assert "기대값" in md and "MDD" in md
    assert "type: 스윙백테스트하니스" in md
