"""간단 백테스트 — '20일선 눌림 후 반등 진입 → 익절/손절, 최대 5거래일' 규칙 성과.

main.run_backtest 와 weekly 브리핑이 공유(순환참조 방지 위해 별도 모듈).
synthetic 데이터면 데모용(실거래 성과 아님) — 출처 컬럼으로 구분.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BacktestSummary:
    n_stocks: int = 0
    total_trades: int = 0
    avg_win_rate: float | None = None
    avg_return: float | None = None   # 거래당 평균수익률 %(기대값)
    real_ratio: float | None = None   # 실데이터(pykrx/yfinance) 비율


def _col(df, name):
    """OHLCV 컬럼을 1D 시리즈로 강제. provider df에 중복 컬럼(MultiIndex 등)이 있으면
    df[name]이 DataFrame(2D)이 되어 .values가 2D → 스칼라화 실패하던 버그 방지."""
    col = df[name]
    if getattr(col, "ndim", 1) > 1:
        col = col.iloc[:, 0]
    return col


def _exit_return(close, entry_idx: int, entry: float, take: float, stop: float, max_hold: int,
                 *, runner: bool, take2: float, trail: float) -> tuple[float, int]:
    """진입(entry_idx, entry가) 후 청산까지 수익률(소수) + 종료 인덱스."""
    high = entry
    partial = False
    locked = 0.0
    i = entry_idx
    j = i + 1
    for j in range(i + 1, min(i + 1 + max_hold, len(close))):
        cur = close[j]
        high = max(high, cur)
        r = (cur - entry) / entry
        if not partial:
            if r <= stop:
                return stop, j
            if r >= take:
                if not runner:
                    return take, j
                partial, locked = True, 0.5 * take          # 절반 익절 잠금
                continue
        else:
            if r >= take2:
                return locked + 0.5 * take2, j               # 잔량 2차 익절
            eff = max(high * (1 - trail / 100), entry)        # 트레일링 + 본전
            if cur <= eff:
                return locked + 0.5 * ((eff - entry) / entry), j
    cur = close[min(j, len(close) - 1)]
    r = (cur - entry) / entry
    return (locked + 0.5 * r) if partial else r, j


def _stock_trades(df, *, take, stop, max_hold, runner, take2, trail, cost, min_tv_eok,
                  require_uptrend=False):
    """단일 종목 df에서 (진입일 'YYYY-MM-DD', 청산수익률 소수, 비용차감) 리스트.

    require_uptrend=True 면 종가>60일선 AND 20일선>60일선(상승배열)에서만 진입(Phase3 필터)."""
    close = _col(df, "close").to_numpy(dtype=float)
    open_ = _col(df, "open").to_numpy(dtype=float)
    vol = _col(df, "volume").to_numpy(dtype=float)
    ma20 = _col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
    ma60 = _col(df, "close").rolling(60, min_periods=1).mean().to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    out: list[tuple[str, float]] = []
    i = 20
    while i < len(close) - 2:                    # i+1 체결 가능해야(룩어헤드 방지)
        tv_eok = close[i] * vol[i] / 1e8
        uptrend = (not require_uptrend) or (close[i] > ma60[i] and ma20[i] > ma60[i])
        if (close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1]
                and tv_eok >= min_tv_eok and open_[i + 1] > 0 and uptrend):
            entry = float(open_[i + 1])           # 다음 봉 시가 체결(0/결측가 방어)
            ret, jend = _exit_return(close, i + 1, entry, take, stop, max_hold,
                                     runner=runner, take2=take2, trail=trail)
            out.append((dates[i + 1], float(ret) - cost))
            i = max(jend, i + 1)
        i += 1
    return out


def _v6_entries_and_blocks(df, regime_by_date, *, take, default_stop, take2,
                           cost, min_tv_eok, policy_table=None):
    """regime 가변 v6 진입 + 반사실(v5 진입 O·v6 차단 O) 산출. 단일 종목.

    반환: (trades, blocks)
      trades: [(entry_date, ret, regime, hold_days)]  — v6 실제 진입.
      blocks: [{entry, ret_v5, regime, reason}]        — v5는 진입했지만 v6가 막은 거래(사후검증용).
    진입 트리거는 v5와 동일(20일선 눌림 후 반등+거래대금). regime 게이트로 차단/트레일 가변.
    """
    from .market_regime import Regime
    from .regime_policy import policy_for
    close = _col(df, "close").to_numpy(dtype=float)
    open_ = _col(df, "open").to_numpy(dtype=float)
    vol = _col(df, "volume").to_numpy(dtype=float)
    ma20 = _col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
    ma60 = _col(df, "close").rolling(60, min_periods=1).mean().to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    trades: list = []
    blocks: list = []
    i = 20
    while i < len(close) - 2:
        tv_eok = close[i] * vol[i] / 1e8
        base_setup = (close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1]
                      and tv_eok >= min_tv_eok and open_[i + 1] > 0)
        if base_setup:
            reg = regime_by_date.get(dates[i], Regime.NEUTRAL)
            pol = policy_for(reg, policy_table)
            stock_up = close[i] > ma60[i] and ma20[i] > ma60[i]
            entry = float(open_[i + 1])
            # v5 결과(반사실용): 추세무관 진입, 트레일 3.0
            ret5, jend5 = _exit_return(close, i + 1, entry, take, default_stop, 20,
                                       runner=True, take2=take2, trail=3.0)
            v5_ret = float(ret5) - cost
            if pol.block_new_entry:
                blocks.append({"entry": dates[i + 1], "ret_v5": v5_ret,
                               "regime": reg.value, "reason": "CRASH차단"})
            elif pol.require_uptrend and not stock_up:
                blocks.append({"entry": dates[i + 1], "ret_v5": v5_ret,
                               "regime": reg.value, "reason": "추세필터"})
            else:
                ret6, jend6 = _exit_return(close, i + 1, entry, take, default_stop, 20,
                                           runner=True, take2=take2, trail=pol.trail_pct)
                trades.append((dates[i + 1], float(ret6) - cost, reg.value,
                               int(jend6 - (i + 1))))
                i = max(jend6, i + 1)
                i += 1
                continue
            i = max(jend5, i + 1)
        i += 1
    return trades, blocks


def _resolve_params(cfg, *, take_pct=None, stop_pct=None, runner: bool | None = None,
                    take2_pct=None, trail_pct=None) -> dict:
    """config + 오버라이드 → simulate/_stock_trades 공용 파라미터.

    runner 미지정(None)이면 라이브 청산과 동기화 — position_manager 가 partial_exit_pct>0 일 때
    항상 부분익절+트레일링을 쓰므로, 백테스트도 그때 runner=on 으로 맞춘다(베이스라인=실제 로직)."""
    take = float(take_pct if take_pct is not None else cfg.get("risk", "take1_pct", default=5.0)) / 100
    stop = float(stop_pct if stop_pct is not None else cfg.get("risk", "default_stop_pct", default=-3.0)) / 100
    take2 = float(take2_pct if take2_pct is not None else cfg.get("risk", "take2_pct", default=8.5)) / 100
    trail = float(trail_pct if trail_pct is not None else cfg.get("risk", "trail_pct", default=3.0))
    if runner is None:
        runner = float(cfg.get("risk", "partial_exit_pct", default=0.5)) > 0
    max_hold = int(cfg.get("risk", "max_hold_days", default=20))
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    min_tv_eok = float(cfg.get("risk", "min_trading_value_eok", default=30))
    require_uptrend = bool(cfg.get("risk", "require_uptrend", default=False))
    return {"take": take, "stop": stop, "take2": take2, "trail": trail, "max_hold": max_hold,
            "cost": 2 * (fee + slip), "min_tv_eok": min_tv_eok, "runner": runner,
            "require_uptrend": require_uptrend}


def simulate(cfg, provider, notes, days: int, take_pct=None, stop_pct=None,
             runner: bool | None = None, take2_pct=None, trail_pct=None):
    """(rows, summary, take, stop). runner 미지정이면 config(partial_exit_pct>0)로 라이브와 동기화."""
    p = _resolve_params(cfg, take_pct=take_pct, stop_pct=stop_pct, runner=runner,
                        take2_pct=take2_pct, trail_pct=trail_pct)
    rows, real, all_rets = [], 0, []
    for n in notes:
        if not n.ticker:
            continue
        df, src = provider.get_ohlcv(n.ticker)
        df = df.tail(days)
        trades = _stock_trades(df, take=p["take"], stop=p["stop"], max_hold=p["max_hold"],
                               runner=p["runner"], take2=p["take2"], trail=p["trail"],
                               cost=p["cost"], min_tv_eok=p["min_tv_eok"],
                               require_uptrend=p["require_uptrend"])
        rets = [r for _, r in trades]
        trades_n = len(rets)
        wr = (sum(1 for r in rets if r > 0) / trades_n * 100) if trades_n else 0
        all_rets += rets
        if src in ("pykrx", "yfinance"):
            real += 1
        rows.append((n.display_name, n.ticker, src, trades_n, wr))

    summary = BacktestSummary(n_stocks=len(rows))
    if rows:
        summary.total_trades = sum(r[3] for r in rows)
        traded = [r[4] for r in rows if r[3] > 0]
        summary.avg_win_rate = round(sum(traded) / len(traded), 1) if traded else None
        summary.avg_return = round(sum(all_rets) / len(all_rets) * 100, 3) if all_rets else None
        summary.real_ratio = round(real / len(rows) * 100, 0)
    return rows, summary, p["take"], p["stop"]


def render_md(rows, days: int, take: float, stop: float, d: str) -> str:
    lines = [
        "---", "type: 스윙백테스트", f"날짜: {d}", "tags: [스윙, 백테스트]", "---",
        f"# 🧪 간단 백테스트 ({days}일) · {d}",
        f"> 규칙: 20일선 눌림 후 반등 진입 → +{take*100:.0f}% 익절 / {stop*100:.0f}% 손절, 최대 5거래일 보유.",
        "> ⚠️ synthetic 데이터면 데모용(실거래 성과 아님). 출처 컬럼 확인.", "",
        "| 종목 | 티커 | 데이터 | 거래수 | 승률 |",
        "|---|---|---|---|---|",
    ]
    for name, tk, src, tr, wr in rows:
        lines.append(f"| {name} | {tk} | {src} | {tr} | {wr:.0f}% |")
    return "\n".join(lines)
