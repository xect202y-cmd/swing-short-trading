"""확장 백테스트 지표 — regime 태깅 거래에서 전체+regime별 성과 산출.

harness.report_from_trades 보다 넓은 지표(CAGR·Sortino·Calmar·최대연속손실·평균보유·월별·regime별).
"""
from __future__ import annotations

import math
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from datetime import date


@dataclass
class TradeRec:
    entry: str        # 'YYYY-MM-DD'
    ret: float        # 청산수익률(소수, 비용차감)
    regime: str       # 'BULL'|'NEUTRAL'|'BEAR'|'CRASH'
    hold_days: int


def _curve(trades, frac_of):
    """시간순 자산곡선(시드 1.0 기준) → (equity_end, mdd, [(entry, equity)])."""
    ordered = sorted(trades, key=lambda t: t.entry)
    eq = peak = 1.0
    mdd = 0.0
    pts = []
    for t in ordered:
        eq *= (1 + frac_of(t) * t.ret)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        pts.append((t.entry, eq))
    return eq, mdd, pts


def _max_consec_losses(trades) -> int:
    ordered = sorted(trades, key=lambda t: t.entry)
    cur = best = 0
    for t in ordered:
        if t.ret <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _monthly(pts) -> dict:
    """자산곡선 포인트 → 월별 수익률%(그 달 마지막 equity를 직전 달 마지막 대비)."""
    by_month = defaultdict(list)
    for d, eq in pts:
        by_month[d[:7]].append(eq)
    out, prev_end = {}, 1.0
    for m in sorted(by_month):
        end = by_month[m][-1]
        out[m] = round((end / prev_end - 1) * 100, 2)
        prev_end = end
    return out


def full_report(trades, *, frac_by_regime: dict | None = None, fixed_frac: float = 0.2) -> dict:
    if not trades:
        return {"n_trades": 0}

    def frac_of(t):
        return frac_by_regime[t.regime] if frac_by_regime else fixed_frac

    rets = [t.ret for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    eq, mdd, pts = _curve(trades, frac_of)
    ordered = sorted(trades, key=lambda t: t.entry)
    span = max((date.fromisoformat(ordered[-1].entry) - date.fromisoformat(ordered[0].entry)).days, 1)
    years = span / 365.0
    total_ret = eq - 1.0
    cagr = (eq ** (1 / years) - 1) if years > 0 and eq > 0 else None

    mean = sum(rets) / len(rets)
    sd = st.pstdev(rets) if len(rets) >= 2 else 0.0
    downside = [min(0.0, x) for x in rets]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 0.0
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    gross_win, gross_loss = sum(wins), -sum(losses)

    by_regime = {}
    for reg in {t.regime for t in trades}:
        sub = [t for t in trades if t.regime == reg]
        _e, _mdd, _ = _curve(sub, frac_of)
        by_regime[reg] = {"n": len(sub),
                          "ret_pct": round((_e - 1) * 100, 2),
                          "mdd_pct": round(_mdd * 100, 2)}

    return {
        "n_trades": len(trades),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
        "mdd_pct": round(mdd * 100, 2),
        "sharpe": round(mean / sd, 3) if sd > 0 else None,
        "sortino": round(mean / dd, 3) if dd > 0 else None,
        "calmar": round((cagr * 100) / abs(mdd * 100), 3) if cagr and mdd < 0 else None,
        "win_rate": round(len(wins) / len(rets) * 100, 1),
        "avg_win_pct": round(avg_win * 100, 3) if avg_win is not None else None,
        "avg_loss_pct": round(avg_loss * 100, 3) if avg_loss is not None else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy_pct": round(mean * 100, 3),
        "realized_rr": round(avg_win / abs(avg_loss), 2) if avg_win and avg_loss else None,
        "avg_hold_days": round(sum(t.hold_days for t in trades) / len(trades), 1),
        "max_consec_losses": _max_consec_losses(trades),
        "monthly_returns": _monthly(pts),
        "by_regime": by_regime,
    }
