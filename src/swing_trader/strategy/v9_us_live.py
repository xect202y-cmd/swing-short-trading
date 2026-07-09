"""v9 US 스윙 라이브 러너 — v7 규칙을 US 시장 유니버스(S&P500∪나스닥100)에 기계 적용.

v9 = v7(정배열 + 20일선 눌림 반등 진입 → 5일선 이탈/대량거래량 음봉/−3% 손절/40일 홀딩 청산)를
관심종목이 아닌 **US 시장 유니버스**에 적용해 미국 거래를 확대한 버전. 골든크로스는 백테스트서 승률
개선이 없어(정배열과 중복) 미채택. min_tv는 달러 기준($20M)으로 보정(억원 필터가 US를 전량 배제하던 문제).

Step 1(현재): 러너 + 가상계좌 코어. 스케줄·디스코드·옵시디언·헤르메스 연동은 Step 2에서.
백테스트(2026-07-09)와 동일 로직 — verify_replay()가 배치 백테스트와 거래 정합성 확인.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

# v7 채택 파라미터(config·run_version_compare와 동일)
STOP = -0.03            # 손절 −3%
VOLSPIKE = 2.5          # 대량거래량 음봉 배수(직전 20일 평균×2.5)
MAX_HOLD = 40           # 최대 보유(거래일)
COST = (1.5 + 5.0) / 10000
MIN_TV_USD = 20e6       # US 유동성 하한 $20M
SEED_USD = 100_000.0
MAX_CONCURRENT = 10     # 동시 보유 상한(동일가중 10%)
_STATE = "v9_us_state.json"


def _emas(df):
    c = df["close"].astype(float)
    return dict(
        close=c.to_numpy(float),
        open=df["open"].astype(float).to_numpy(float),
        vol=df["volume"].astype(float).to_numpy(float),
        ma5=c.rolling(5, min_periods=1).mean().to_numpy(float),
        ma20=c.rolling(20, min_periods=1).mean().to_numpy(float),
        ma60=c.rolling(60, min_periods=1).mean().to_numpy(float),
        va20=df["volume"].astype(float).rolling(20, min_periods=5).mean().to_numpy(float),
        dates=[d.strftime("%Y-%m-%d") for d in df.index],
    )


def entry_ok(a, i) -> bool:
    """v7 진입: 정배열(종가>60일선·20>60일선) + 20일선 눌림 후 반등 + 유동성. (i+1 시가 체결 전제)"""
    if i < 60 or i + 1 >= len(a["close"]):
        return False
    uptrend = a["close"][i] > a["ma60"][i] and a["ma20"][i] > a["ma60"][i]
    pullback_bounce = a["close"][i] <= a["ma20"][i] * 1.01 and a["close"][i] > a["close"][i - 1]
    tv_ok = a["close"][i] * a["vol"][i] >= MIN_TV_USD
    return uptrend and pullback_bounce and tv_ok and a["open"][i + 1] > 0


def exit_today(a, j, entry: float, bars_held: int) -> tuple[bool, float, str]:
    """v7 청산(증분 1일 판정) — _v7_exit(배치)와 동일 조건. → (청산?, 수익률, 사유)."""
    r = (a["close"][j] - entry) / entry
    if r <= STOP:
        return True, STOP, "손절 −3%"
    down_vol = a["close"][j] < a["open"][j] and a["vol"][j] >= a["va20"][j] * VOLSPIKE
    if a["close"][j] < a["ma5"][j]:
        return True, r, "5일선 이탈"
    if down_vol:
        return True, r, "대량거래량 음봉"
    if bars_held >= MAX_HOLD:
        return True, r, "최대 보유(40일)"
    return False, r, ""


# ── 가상계좌 상태 ──
def load_state(state_dir: Path) -> dict:
    p = Path(state_dir) / _STATE
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"asOf": "", "seed": SEED_USD, "cash": SEED_USD, "realized": 0.0, "open": [], "trades": []}


def save_state(state_dir: Path, st: dict) -> None:
    st["trades"] = st["trades"][-400:]
    (Path(state_dir) / _STATE).write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _bar_index(a, d: str) -> int | None:
    try:
        return a["dates"].index(d)
    except ValueError:
        return None


def step_day(st: dict, arrs: dict, d: str) -> dict:
    """하루 사이클: (1) 보유 청산 판정 → (2) 신규 진입. arrs: {ticker: _emas(df)}. 확정봉 기준."""
    # (1) 청산 pass
    still = []
    for pos in st["open"]:
        a = arrs.get(pos["ticker"])
        j = _bar_index(a, d) if a else None
        if j is None:
            still.append(pos)
            continue
        held = pos["bars_held"] + 1
        done, r, why = exit_today(a, j, pos["entry"], held)
        if done:
            pnl = pos["qty"] * pos["entry"] * (r - COST)
            st["cash"] += pos["qty"] * pos["entry"] * (1 + r - COST)
            st["realized"] += pnl
            st["trades"].append({"ticker": pos["ticker"], "entry_date": pos["entry_date"],
                                 "exit_date": d, "entry": round(pos["entry"], 2),
                                 "ret_pct": round(r * 100, 2), "pnl": round(pnl, 2), "reason": why})
        else:
            pos["bars_held"] = held
            still.append(pos)
    st["open"] = still

    # (2) 진입 pass — 슬롯 여유만큼, 등락 무관 스캔순
    held_tickers = {p["ticker"] for p in st["open"]}
    slots = MAX_CONCURRENT - len(st["open"])
    if slots > 0:
        alloc = st["seed"] * (1.0 / MAX_CONCURRENT)
        for tk, a in arrs.items():
            if slots <= 0:
                break
            if tk in held_tickers:
                continue
            i = _bar_index(a, d)
            if i is None or not entry_ok(a, i):
                continue
            entry_px = float(a["open"][i + 1]) if i + 1 < len(a["open"]) else None
            if not entry_px or entry_px <= 0:
                continue
            budget = min(alloc, st["cash"])
            qty = int(budget // entry_px)
            if qty < 1:
                continue
            st["cash"] -= qty * entry_px
            st["open"].append({"ticker": tk, "entry_date": d, "entry": entry_px,
                               "qty": qty, "bars_held": 0})
            held_tickers.add(tk)
            slots -= 1
    st["asOf"] = d
    return st
