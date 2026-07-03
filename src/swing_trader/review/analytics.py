"""성과 분석 — equity 곡선 / 청산거래 원장 적재 + 지표 계산.

지표: 누적수익율·승률·손익비·profit factor·MDD·점수대별 승률·룰준수율·보유일분포.
이 지표들이 '백테스트/페이퍼가 잘 되는지'를 정량으로 보여준다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def record_equity(state_dir: Path, d: str, cash: float, holdings_value: float, seed: float) -> None:
    path = state_dir / "equity_history.json"
    rows = _load(path)
    equity = round(cash + holdings_value, 0)
    row = {"date": d, "cash": round(cash, 0), "holdings": round(holdings_value, 0),
           "equity": equity, "return_pct": round((equity - seed) / seed * 100, 2)}
    rows = [r for r in rows if r.get("date") != d] + [row]   # 같은날 갱신
    rows.sort(key=lambda r: r["date"])
    _save(path, rows)


def record_closed_trades(state_dir: Path, trades: list[dict]) -> None:
    if not trades:
        return
    path = state_dir / "closed_trades.json"
    rows = _load(path) + trades
    _save(path, rows)


def record_logic_review(state_dir: Path, review: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "logic_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")


def load_equity(state_dir: Path) -> list[dict]:
    return _load(state_dir / "equity_history.json")


def load_closed(state_dir: Path) -> list[dict]:
    return _load(state_dir / "closed_trades.json")


def realized_since(state_dir: Path, since_iso: str, extra: list[dict] | None = None) -> float:
    """exit_date 가 since_iso(YYYY-MM-DD) 이후인 청산거래의 실현손익 합.

    일/주 손실 한도용 — broker.realized_pnl(전체 누적)과 달리 '그 날/그 주' 창만 본다.
    extra: 아직 closed_trades.json 에 안 들어간 이번 런의 청산거래(중복합산 방지).
    """
    def _sum(rows):
        return sum(float(r.get("pnl", 0) or 0) for r in rows
                   if str(r.get("exit_date", "")) >= since_iso)
    return round(_sum(load_closed(state_dir)) + _sum(extra or []), 2)


@dataclass
class Metrics:
    n_closed: int = 0
    win_rate: float | None = None
    avg_win_pct: float | None = None
    avg_loss_pct: float | None = None
    profit_factor: float | None = None
    total_realized: float = 0.0
    return_pct: float | None = None        # 시드 대비 현재 equity
    max_drawdown_pct: float | None = None
    rule_adherence: float | None = None    # 손절 준수율
    target_hit: int = 0
    stop_hit: int = 0
    by_score_band: dict[str, dict] = field(default_factory=dict)
    avg_hold_days: float | None = None


def compound_projection(equity: list[dict], seed: float) -> dict | None:
    """현재까지 실현된 페이스를 단순 외삽해 주/월/연 복리 추정. 표본 적으면 부정확(추정치)."""
    from datetime import date as _date
    if len(equity) < 2:
        return None
    try:
        d0 = _date.fromisoformat(equity[0]["date"])
        d1 = _date.fromisoformat(equity[-1]["date"])
    except (ValueError, KeyError):
        return None
    days = max((d1 - d0).days, 1)
    total = equity[-1].get("equity", seed) / seed - 1
    out: dict = {"days": days, "total_pct": round(total * 100, 2)}
    if days >= 3 and total > -0.99:
        out["weekly_pct"] = round(((1 + total) ** (7 / days) - 1) * 100, 2)
        out["monthly_pct"] = round(((1 + total) ** (30 / days) - 1) * 100, 2)
        out["annual_pct"] = round(((1 + total) ** (365 / days) - 1) * 100, 1)
        out["annual_won"] = round(seed * (1 + total) ** (365 / days), 0)
    return out


def _max_drawdown(equity: list[dict]) -> float | None:
    if len(equity) < 2:
        return None
    peak = -1e18
    mdd = 0.0
    for r in equity:
        e = r.get("equity", 0)
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, (e - peak) / peak * 100)
    return round(mdd, 2)


def compute_metrics(closed: list[dict], equity: list[dict], seed: float) -> Metrics:
    m = Metrics(n_closed=len(closed))
    if closed:
        wins = [t for t in closed if t.get("pnl", 0) > 0]
        losses = [t for t in closed if t.get("pnl", 0) <= 0]
        m.win_rate = round(len(wins) / len(closed) * 100, 1)
        m.avg_win_pct = round(sum(t.get("return_pct", 0) for t in wins) / len(wins), 2) if wins else None
        m.avg_loss_pct = round(sum(t.get("return_pct", 0) for t in losses) / len(losses), 2) if losses else None
        gross_win = sum(t.get("pnl", 0) for t in wins)
        gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
        m.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
        m.total_realized = round(sum(t.get("pnl", 0) for t in closed), 0)
        m.target_hit = sum(1 for t in closed if "목표" in (t.get("exit_reason", "") or ""))
        m.stop_hit = sum(1 for t in closed if "손절" in (t.get("exit_reason", "") or ""))
        respected = sum(1 for t in closed if t.get("stop_respected", True))
        m.rule_adherence = round(respected / len(closed) * 100, 1)
        holds = [t.get("hold_days") for t in closed if t.get("hold_days") is not None]
        m.avg_hold_days = round(sum(holds) / len(holds), 1) if holds else None
        # 점수대별 승률 (점수 로직 검증)
        bands = {"60-69": [], "70-79": [], "80+": []}
        for t in closed:
            sc = t.get("entry_score")
            if sc is None:
                continue
            key = "80+" if sc >= 80 else "70-79" if sc >= 70 else "60-69"
            bands[key].append(t)
        for k, ts in bands.items():
            if ts:
                w = sum(1 for t in ts if t.get("pnl", 0) > 0)
                m.by_score_band[k] = {"n": len(ts), "win_rate": round(w / len(ts) * 100, 0),
                                      "avg_ret": round(sum(t.get("return_pct", 0) for t in ts) / len(ts), 1)}
    if equity:
        m.return_pct = equity[-1].get("return_pct")
    m.max_drawdown_pct = _max_drawdown(equity)
    return m
