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
                  require_uptrend=False, with_meta=False):
    """단일 종목 df에서 (진입일 'YYYY-MM-DD', 청산수익률 소수, 비용차감) 리스트.

    require_uptrend=True 면 종가>60일선 AND 20일선>60일선(상승배열)에서만 진입(Phase3 필터).
    with_meta=True 면 슬롯제한 포트폴리오 리플레이용 5-튜플 (진입일, 수익률, 청산일, 진입가, 모멘텀)."""
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
            if with_meta:
                mom = (close[i] / ma60[i] - 1) if ma60[i] > 0 else 0.0
                out.append((dates[i + 1], float(ret) - cost, dates[jend], entry, float(mom)))
            else:
                out.append((dates[i + 1], float(ret) - cost))
            i = max(jend, i + 1)
        i += 1
    return out


def _v7_exit(close, open_, vol, ma5, va20, entry_idx: int, entry: float,
             *, stop: float, take1, volspike: float, max_hold: int) -> tuple[float, int]:
    """v7 청산 — 5일선 이탈 or 대량거래량 음봉까지 홀딩(추세추종). take1 지정 시 절반 부분익절."""
    partial = False
    locked = 0.0
    j = entry_idx
    for j in range(entry_idx + 1, min(entry_idx + 1 + max_hold, len(close))):
        r = (close[j] - entry) / entry
        if r <= stop:
            return (locked + 0.5 * stop) if partial else stop, j
        if take1 is not None and not partial and r >= take1:
            partial, locked = True, 0.5 * take1        # 절반 부분익절 잠금
            continue
        down_vol = close[j] < open_[j] and vol[j] >= va20[j] * volspike   # 대량거래량 음봉
        if close[j] < ma5[j] or down_vol:                                # 5일선 이탈 or 대량음봉 → 매도
            return (locked + 0.5 * r) if partial else r, j
    r = (close[min(j, len(close) - 1)] - entry) / entry
    return (locked + 0.5 * r) if partial else r, j


def _v7_stock_trades(df, *, stop, take1, volspike, max_hold, cost, min_tv_eok,
                     require_uptrend=True, momentum_min_pct=0.0, with_meta=False):
    """v7 — 강세(정배열) 종목의 20일선 눌림 반등 진입 → 5일선 이탈/대량거래량 음봉까지 홀딩.

    유튜브 스윙 3편(Farley 마스터 스윙·성경호 신고가 눌림) 수렴 기법:
    ① 신고가 강세주(정배열 프록시: 종가>60일선·20>60일선)만 대상
    ② 20일선 눌림 후 반등에 진입(v5 검증 타점 재사용)
    ③ 익절 고정 없이 5일선 이탈까지 홀딩(승자 달리게) — v5의 +6% 고정익절이 승자를 자르던 문제 해소
    ④ 대량거래량 음봉 = 세력 이탈 신호로 매도, 손절 stop.
    OOS 검증서 v5 대비 기대값·PF 개선(승률은 낮으나 승자 수익 큼)."""
    close = _col(df, "close").to_numpy(dtype=float)
    open_ = _col(df, "open").to_numpy(dtype=float)
    vol = _col(df, "volume").to_numpy(dtype=float)
    ma5 = _col(df, "close").rolling(5, min_periods=1).mean().to_numpy(dtype=float)
    ma20 = _col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
    ma60 = _col(df, "close").rolling(60, min_periods=1).mean().to_numpy(dtype=float)
    va20 = _col(df, "volume").rolling(20, min_periods=5).mean().to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    out: list[tuple[str, float]] = []
    i = 20
    while i < len(close) - 2:
        tv_eok = close[i] * vol[i] / 1e8
        uptrend = (not require_uptrend) or (close[i] > ma60[i] and ma20[i] > ma60[i])
        # v9 진입 품질필터: 종가가 60일선 대비 momentum_min_pct%↑ 강세일 때만(0이면 v7과 동일).
        momentum_ok = momentum_min_pct <= 0 or (ma60[i] > 0 and (close[i] / ma60[i] - 1) * 100 >= momentum_min_pct)
        if (close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1]
                and tv_eok >= min_tv_eok and open_[i + 1] > 0 and uptrend and momentum_ok):
            entry = float(open_[i + 1])
            ret, jend = _v7_exit(close, open_, vol, ma5, va20, i + 1, entry,
                                 stop=stop, take1=take1, volspike=volspike, max_hold=max_hold)
            if with_meta:
                mom = (close[i] / ma60[i] - 1) if ma60[i] > 0 else 0.0
                out.append((dates[i + 1], float(ret) - cost, dates[jend], entry, float(mom)))
            else:
                out.append((dates[i + 1], float(ret) - cost))
            i = max(jend, i + 1)
        i += 1
    return out


def _v8_stock_trades(df, *, take, divisions, max_cycle_days, cost, min_tv_eok,
                     require_uptrend=True, with_meta=False):
    """v8 — v7 진입(정배열·20일선 눌림 반등) 후 '무한매수법'(라오어) 운용.

    유튜브 무한매수법 규칙(2026-07-06, JkkJd4Wdb_A 10:26~14:00):
    ① 진입 게이트는 v7 그대로(강세주 눌림 반등). 다음 봉 시가에 1분할 첫 매수 = 사이클 시작.
    ② 매일 종가에 1분할씩 기계적 추가 매수(LOC 근사·가격 무관 DCA) — divisions 회 상한.
       하락하면 그대로 사서 평단(정액매수라 조화평균)을 계속 낮춘다.
    ③ 매일 '전일까지의 평단' 대비 +take 도달(당일 고가 기준) 시 전량 익절(체결가=평단×(1+take)),
       사이클 리셋. 당일 매수분은 익절 판정에 미포함(룩어헤드 방지).
    ④ 손절 없음(원전 규칙). 단 백테스트 유한성 위해 max_cycle_days 미도달 시 종가 청산.
    비용은 기존 버전들과 동일 규약(사이클=거래 1건당 왕복 cost 1회) — 사이징 중립 비교 유지.
    반환: [(사이클 시작일 'YYYY-MM-DD', 사이클 수익률 소수 - cost)].
    ※ 원전 대상은 3배 레버리지 ETF — 여기선 v7 유니버스(개별주)에 이식해 검증.
    """
    close = _col(df, "close").to_numpy(dtype=float)
    open_ = _col(df, "open").to_numpy(dtype=float)
    high = _col(df, "high").to_numpy(dtype=float)
    vol = _col(df, "volume").to_numpy(dtype=float)
    ma20 = _col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
    ma60 = _col(df, "close").rolling(60, min_periods=1).mean().to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    out: list[tuple[str, float]] = []
    i = 20
    while i < len(close) - 2:
        tv_eok = close[i] * vol[i] / 1e8
        uptrend = (not require_uptrend) or (close[i] > ma60[i] and ma20[i] > ma60[i])
        if (close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1]
                and tv_eok >= min_tv_eok and open_[i + 1] > 0 and uptrend):
            start = i + 1
            invested, shares, buys = 1.0, 1.0 / float(open_[start]), 1   # 1분할 금액=1 정규화
            end_idx, ret = start, None
            for j in range(start + 1, min(start + max_cycle_days, len(close))):
                end_idx = j
                avg = invested / shares                     # 전일까지의 평단
                if high[j] >= avg * (1 + take):             # 지정가 익절 체결 가정
                    ret = take
                    break
                if buys < divisions and close[j] > 0:       # 미도달 → 종가 1분할 추가 매수
                    invested += 1.0
                    shares += 1.0 / close[j]
                    buys += 1
            if ret is None:                                 # 사이클 상한/데이터 끝 → 종가 청산
                ret = close[end_idx] / (invested / shares) - 1
            if with_meta:
                mom = (close[i] / ma60[i] - 1) if ma60[i] > 0 else 0.0
                out.append((dates[start], float(ret) - cost, dates[end_idx], float(open_[start]), float(mom)))
            else:
                out.append((dates[start], float(ret) - cost))
            i = max(end_idx, i + 1)
        i += 1
    return out


def _v6_ohlc(df):
    """v6 진입/차단 계산 공용 배열 추출(1D 강제)."""
    close = _col(df, "close").to_numpy(dtype=float)
    open_ = _col(df, "open").to_numpy(dtype=float)
    vol = _col(df, "volume").to_numpy(dtype=float)
    ma20 = _col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
    ma60 = _col(df, "close").rolling(60, min_periods=1).mean().to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    return close, open_, vol, ma20, ma60, dates


def _v6_stock_trades(df, regime_by_date, *, take, default_stop, take2,
                     cost, min_tv_eok, policy_table=None, with_meta=False):
    """v6 를 '독립 전략'으로 백테스트 — 진입일 regime 으로 차단/트레일 가변.

    차단 봉에서는 포지션이 없으므로 i+=1 로 계속 스캔(다음 유효 setup 을 놓치지 않음).
    반환: [(entry_date, ret, regime, hold_days)]. with_meta=True 면 슬롯 리플레이용
    5-튜플 (진입일, 수익률, 청산일, 진입가, 모멘텀).
    """
    from .market_regime import Regime
    from .regime_policy import policy_for
    close, open_, vol, ma20, ma60, dates = _v6_ohlc(df)
    trades: list = []
    i = 20
    while i < len(close) - 2:
        tv_eok = close[i] * vol[i] / 1e8
        setup = (close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1]
                 and tv_eok >= min_tv_eok and open_[i + 1] > 0)
        if setup:
            reg = regime_by_date.get(dates[i], Regime.NEUTRAL)
            pol = policy_for(reg, policy_table)
            stock_up = close[i] > ma60[i] and ma20[i] > ma60[i]
            blocked = pol.block_new_entry or (pol.require_uptrend and not stock_up)
            if not blocked:
                entry = float(open_[i + 1])
                ret6, jend6 = _exit_return(close, i + 1, entry, take, default_stop, 20,
                                           runner=True, take2=take2, trail=pol.trail_pct)
                if with_meta:
                    mom = (close[i] / ma60[i] - 1) if ma60[i] > 0 else 0.0
                    trades.append((dates[i + 1], float(ret6) - cost, dates[jend6], entry, float(mom)))
                else:
                    trades.append((dates[i + 1], float(ret6) - cost, reg.value, int(jend6 - (i + 1))))
                i = max(jend6, i + 1)
                i += 1
                continue
        i += 1
    return trades


def _v5_blocks_by_v6(df, regime_by_date, *, take, default_stop, take2,
                     cost, min_tv_eok, policy_table=None):
    """반사실 — v5 진입(추세무관·트레일3.0) 집합 중 v6 가 막는 거래.

    v5 와 동일 cadence(_stock_trades require_uptrend=False) 를 그대로 재현 → blocks 는
    v5 진입 집합의 '정확한 부분집합'. 반환: [{entry, ret_v5, regime, reason}].
    """
    from .market_regime import Regime
    from .regime_policy import policy_for
    close, open_, vol, ma20, ma60, dates = _v6_ohlc(df)
    blocks: list = []
    i = 20
    while i < len(close) - 2:
        tv_eok = close[i] * vol[i] / 1e8
        setup = (close[i] <= ma20[i] * 1.01 and close[i] > close[i - 1]
                 and tv_eok >= min_tv_eok and open_[i + 1] > 0)
        if setup:                                    # 이 봉이 v5 의 실제 진입
            reg = regime_by_date.get(dates[i], Regime.NEUTRAL)
            pol = policy_for(reg, policy_table)
            stock_up = close[i] > ma60[i] and ma20[i] > ma60[i]
            entry = float(open_[i + 1])
            ret5, jend5 = _exit_return(close, i + 1, entry, take, default_stop, 20,
                                       runner=True, take2=take2, trail=3.0)
            v5_ret = float(ret5) - cost
            if pol.block_new_entry:
                blocks.append({"entry": dates[i + 1], "ret_v5": v5_ret,
                               "regime": reg.value, "reason": "CRASH차단"})
            elif pol.require_uptrend and not stock_up:
                blocks.append({"entry": dates[i + 1], "ret_v5": v5_ret,
                               "regime": reg.value, "reason": "추세필터"})
            i = max(jend5, i + 1)                     # v5 cadence 유지(보유기간 스킵)
        i += 1
    return blocks


def _v6_entries_and_blocks(df, regime_by_date, *, take, default_stop, take2,
                           cost, min_tv_eok, policy_table=None):
    """(v6 독립 거래, v5진입-v6차단 반사실) 을 각각 올바른 cadence로 산출."""
    kw = dict(take=take, default_stop=default_stop, take2=take2, cost=cost,
              min_tv_eok=min_tv_eok, policy_table=policy_table)
    return (_v6_stock_trades(df, regime_by_date, **kw),
            _v5_blocks_by_v6(df, regime_by_date, **kw))


def _resolve_params(cfg, *, take_pct=None, stop_pct=None, runner: bool | None = None,
                    take2_pct=None, trail_pct=None,
                    max_hold=None, require_uptrend=None, min_tv_eok=None) -> dict:
    """config + 오버라이드 → simulate/_stock_trades 공용 파라미터.

    runner 미지정(None)이면 라이브 청산과 동기화 — position_manager 가 partial_exit_pct>0 일 때
    항상 부분익절+트레일링을 쓰므로, 백테스트도 그때 runner=on 으로 맞춘다(베이스라인=실제 로직)."""
    take = float(take_pct if take_pct is not None else cfg.get("risk", "take1_pct", default=5.0)) / 100
    stop = float(stop_pct if stop_pct is not None else cfg.get("risk", "default_stop_pct", default=-3.0)) / 100
    take2 = float(take2_pct if take2_pct is not None else cfg.get("risk", "take2_pct", default=8.5)) / 100
    trail = float(trail_pct if trail_pct is not None else cfg.get("risk", "trail_pct", default=3.0))
    if runner is None:
        runner = float(cfg.get("risk", "partial_exit_pct", default=0.5)) > 0
    max_hold = int(max_hold if max_hold is not None else cfg.get("risk", "max_hold_days", default=20))
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    min_tv_eok = float(min_tv_eok if min_tv_eok is not None
                       else cfg.get("risk", "min_trading_value_eok", default=30))
    require_uptrend = bool(require_uptrend if require_uptrend is not None
                           else cfg.get("risk", "require_uptrend", default=False))
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
