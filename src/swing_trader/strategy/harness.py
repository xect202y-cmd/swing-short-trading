"""백테스트 검증 하니스 — 날짜 거래 → 지표 리포트 + 인샘플/아웃오브샘플 분할 + A/B 판정.

simulate(요약·표)와 분리: 여기선 개별 거래(날짜 포함)를 모아 기대값·손익비·PF·MDD·Sharpe를
산출하고, 시간순 홀드아웃(OOS)으로 과최적화를 적발한다.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from datetime import date, timedelta

from ..market.data_provider import DataProvider


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
    cum = peak = 0.0
    mdd = 0.0
    for t in ordered:
        cum += t.ret                  # flat-bet(동일 베팅): 가산 누적, 복리 아님(Phase4 사이징 전까지)
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    r.max_drawdown = round(mdd * 100, 2)
    if len(rets) >= 2:
        sd = st.pstdev(rets)
        r.sharpe = round((sum(rets) / len(rets)) / sd, 3) if sd > 0 else None
    r.start, r.end = ordered[0].entry, ordered[-1].entry
    span = (date.fromisoformat(r.end) - date.fromisoformat(r.start)).days
    if span > 0:
        r.trades_per_year = round(len(trades) / span * 365, 1)
    return r


def split_oos(trades: list[Trade], frac: float = 0.3) -> tuple[list[Trade], list[Trade]]:
    """entry 날짜 기준 시간순 분할 → (인샘플, 아웃오브샘플). 뒤 frac 기간이 OOS 홀드아웃."""
    if not trades:
        return [], []
    ordered = sorted(trades, key=lambda t: t.entry)
    start = date.fromisoformat(ordered[0].entry)
    end = date.fromisoformat(ordered[-1].entry)
    span = (end - start).days
    if span <= 0:
        return ordered, []
    cut = (start + timedelta(days=int(span * (1 - frac)))).isoformat()
    is_ = [t for t in ordered if t.entry < cut]
    oos = [t for t in ordered if t.entry >= cut]
    return is_, oos


def simulate_trades(cfg, provider, notes, days: int, **params) -> list[Trade]:
    """전 종목 백테스트 → 날짜 붙은 Trade 리스트. params 는 _resolve_params 오버라이드
    (take_pct/stop_pct/runner/take2_pct/trail_pct)."""
    from . import backtest as _BT
    p = _BT._resolve_params(cfg, **params)
    trades: list[Trade] = []
    for n in notes:
        if not n.ticker:
            continue
        df, _src = provider.get_ohlcv(n.ticker)
        df = df.tail(days)
        for d, r in _BT._stock_trades(df, take=p["take"], stop=p["stop"], max_hold=p["max_hold"],
                                      runner=p["runner"], take2=p["take2"], trail=p["trail"],
                                      cost=p["cost"], min_tv_eok=p["min_tv_eok"]):
            trades.append(Trade(n.ticker, d, r))
    return trades


@dataclass
class ABResult:
    sample_ok: bool
    n_oos: int
    base_is: BacktestReport
    base_oos: BacktestReport
    cand_is: BacktestReport
    cand_oos: BacktestReport
    verdict: str   # improve | neutral | worse | insufficient


def _judge(base: BacktestReport, cand: BacktestReport) -> str:
    """OOS 기준 판정. 미세차이는 노이즈로 간주(기대값 ±0.02%p, Sharpe ±0.05 마진)."""
    be, ce = base.expectancy or 0.0, cand.expectancy or 0.0
    bs, cs = base.sharpe or 0.0, cand.sharpe or 0.0
    better = (ce > be + 0.02) or (cs > bs + 0.05)
    worse = (ce < be - 0.02) and (cs < bs - 0.05)
    if worse:
        return "worse"
    if better:
        return "improve"
    return "neutral"


def compare(cfg, provider, notes, days: int, baseline: dict, candidate: dict,
            oos_fraction=None, min_oos=None) -> ABResult:
    frac = oos_fraction if oos_fraction is not None else float(cfg.get("backtest", "oos_fraction", default=0.3))
    floor = min_oos if min_oos is not None else int(cfg.get("backtest", "min_oos_trades", default=100))
    b_is, b_oos = split_oos(simulate_trades(cfg, provider, notes, days, **baseline), frac)
    c_is, c_oos = split_oos(simulate_trades(cfg, provider, notes, days, **candidate), frac)
    base_is, base_oos = report_from_trades(b_is), report_from_trades(b_oos)
    cand_is, cand_oos = report_from_trades(c_is), report_from_trades(c_oos)
    n_oos = min(base_oos.n_trades, cand_oos.n_trades)
    if n_oos < floor:
        return ABResult(False, n_oos, base_is, base_oos, cand_is, cand_oos, "insufficient")
    return ABResult(True, n_oos, base_is, base_oos, cand_is, cand_oos, _judge(base_oos, cand_oos))


def _fmt(v, suffix="%"):
    return "—" if v is None else f"{v:g}{suffix}"


def _report_row(label: str, r: BacktestReport) -> str:
    return (f"| {label} | {r.n_trades} | {_fmt(r.win_rate)} | {_fmt(r.expectancy)} | "
            f"{_fmt(r.profit_factor, '')} | {_fmt(r.max_drawdown)} | {_fmt(r.sharpe, '')} | "
            f"{_fmt(r.trades_per_year, '')} |")


def render_report_md(title: str, is_rep: BacktestReport, oos_rep: BacktestReport, d: str) -> str:
    gap = None
    if is_rep.expectancy is not None and oos_rep.expectancy is not None:
        gap = round(oos_rep.expectancy - is_rep.expectancy, 3)
    lines = [
        "---", "type: 스윙백테스트하니스", f"날짜: {d}", "tags: [스윙, 백테스트, 검증]", "---",
        f"# 🧪 {title} · {d}",
        "> 규칙: 20일선 눌림 후 반등 진입 → 익절/손절·트레일링. 비용(수수료+슬리피지) 차감.",
        "> 판정은 **아웃오브샘플(OOS)** 기준. IS↔OOS 기대값 격차가 크면 과최적화 신호.", "",
        "| 구간 | 거래수 | 승률 | 기대값 | PF | MDD | Sharpe | 연거래 |",
        "|---|---|---|---|---|---|---|---|",
        _report_row("인샘플(IS)", is_rep),
        _report_row("아웃오브샘플(OOS)", oos_rep),
    ]
    if gap is not None:
        sign = "✅ 견고" if gap >= -0.2 else "⚠️ 과최적화 의심"
        lines += ["", f"**IS→OOS 기대값 격차**: {gap:+g}%p · {sign}"]
    return "\n".join(lines)


def backtest_provider(cfg) -> DataProvider:
    """백테스트 전용 provider — backtest.lookback_days(기본 500)로 더 긴 히스토리 fetch."""
    from ..market.fx import get_usdkrw
    md = cfg.get("market_data", default={})
    look = int(cfg.get("backtest", "lookback_days", default=500))
    fx = get_usdkrw(float(md.get("fx_usdkrw", 1400)))
    return DataProvider(provider=md.get("provider", "auto"), lookback_days=look, fx_usdkrw=fx)
