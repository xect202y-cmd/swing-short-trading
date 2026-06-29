"""백테스트 검증 하니스 — 날짜 거래 → 지표 리포트 + 인샘플/아웃오브샘플 분할 + A/B 판정.

simulate(요약·표)와 분리: 여기선 개별 거래(날짜 포함)를 모아 기대값·손익비·PF·MDD·Sharpe를
산출하고, 시간순 홀드아웃(OOS)으로 과최적화를 적발한다.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Trade:
    ticker: str
    entry: str   # 'YYYY-MM-DD' (다음봉 시가 체결일)
    ret: float   # 청산수익률(소수, 비용차감)


@dataclass
class BacktestReport:
    n_trades: int = 0
    win_rate: float | None = None        # %
    avg_win: float | None = None         # % (양수)
    avg_loss: float | None = None        # % (음수)
    expectancy: float | None = None      # 거래당 평균 %
    profit_factor: float | None = None
    max_drawdown: float | None = None    # % (음수)
    sharpe: float | None = None          # 거래당 평균/표준편차
    trades_per_year: float | None = None
    start: str | None = None
    end: str | None = None


def report_from_trades(trades: list[Trade]) -> BacktestReport:
    r = BacktestReport(n_trades=len(trades))
    if not trades:
        return r
    rets = [t.ret for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    r.win_rate = round(len(wins) / len(rets) * 100, 1)
    r.avg_win = round(sum(wins) / len(wins) * 100, 3) if wins else None
    r.avg_loss = round(sum(losses) / len(losses) * 100, 3) if losses else None
    r.expectancy = round(sum(rets) / len(rets) * 100, 3)
    gross_win, gross_loss = sum(wins), -sum(losses)
    r.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    ordered = sorted(trades, key=lambda t: t.entry)
    eq = peak = 1.0
    mdd = 0.0
    for t in ordered:
        eq *= (1 + t.ret)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    r.max_drawdown = round(mdd * 100, 2)
    if len(rets) >= 2:
        sd = st.pstdev(rets)
        r.sharpe = round((sum(rets) / len(rets)) / sd, 3) if sd > 0 else None
    r.start, r.end = ordered[0].entry, ordered[-1].entry
    span = (date.fromisoformat(r.end) - date.fromisoformat(r.start)).days
    if span > 0:
        r.trades_per_year = round(len(trades) / span * 365, 1)
    return r
