from swing_trader.review.v6_doc import render_v6_doc

_SAMPLE = {
    "as_of": "2026-07-04", "seed": 5000000, "oos_start": "2024-06-04",
    "oos_end": "2026-07-01", "lookback_days": 500,
    "versions": [
        {"label": "v4", "title": "t", "edge": {"n_trades": 300, "win_rate": 37.0,
            "expectancy_pct": 0.6, "profit_factor": 1.4, "mdd_pct": -16.0, "sharpe": 0.1,
            "sortino": 0.2, "calmar": 0.5, "total_return_pct": 60.0, "cagr_pct": 20.0,
            "avg_win_pct": 3.0, "avg_loss_pct": -2.0, "realized_rr": 1.5, "avg_hold_days": 4.0,
            "max_consec_losses": 5, "by_regime": {"BULL": {"n": 200, "ret_pct": 40.0, "mdd_pct": -10.0}},
            "monthly_returns": {}},
         "as_traded": {"total_return_pct": 60.0, "cagr_pct": 20.0, "mdd_pct": -16.0,
            "calmar": 0.5, "by_regime": {}}},
        {"label": "v6", "title": "hybrid", "edge": {"n_trades": 400, "win_rate": 38.0,
            "expectancy_pct": 0.7, "profit_factor": 1.5, "mdd_pct": -12.0, "sharpe": 0.15,
            "sortino": 0.25, "calmar": 0.8, "total_return_pct": 90.0, "cagr_pct": 30.0,
            "avg_win_pct": 3.1, "avg_loss_pct": -1.9, "realized_rr": 1.6, "avg_hold_days": 4.2,
            "max_consec_losses": 4, "by_regime": {"BEAR": {"n": 50, "ret_pct": -2.0, "mdd_pct": -6.0}},
            "monthly_returns": {}},
         "as_traded": {"total_return_pct": 88.0, "cagr_pct": 29.0, "mdd_pct": -9.0,
            "calmar": 1.1, "by_regime": {}}},
    ],
    "counterfactual": {"n": 10, "avg_ret_pct": -1.2, "helped_pct": 60.0,
                       "by_reason": {"CRASH차단": {"n": 4, "avg_ret_pct": -3.0}}},
}


def test_all_sections_present():
    md = render_v6_doc(_SAMPLE)
    for h in ["전략 개요", "대비 변경점", "regime 판별", "regime별 설정", "진입 조건",
              "차단 조건", "트레일링", "포지션 사이징", "백테스트 비교", "regime별 성과",
              "반사실", "추가 개선"]:
        assert h in md
    # 비교표에 세 지표가 실제로 렌더되는지(대표값 스팟체크)
    assert "v6" in md and "CRASH차단" in md
