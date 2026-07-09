"""V1 US 스윙 라이브 러너 — v7 추세추종을 US 시장(S&P500∪관심종목)에 기계 적용.

'V1 US' = US 스윙 계보 1번(구 v9). 원화(KRW) 운용 + 실시간 환율. **현금은 중/단기 스윙과 공유** —
`open_positions.json`의 cash(500만 풀)에서 원화 차감/환입. US 포지션만 `v1_us_state.json`에 별도 저장
(KR 포지션 미변경). 06시(US)·09시(KR) 시차 실행이라 충돌 없음.

로직: v7 그대로(정배열+20일선 눌림 반등 → 5일선이탈/대량음봉/−3%손절/40일 청산). 진입 랭킹=모멘텀.
체결=확정봉 종가(MOC). 손익은 종목수익 × 환율(진입FX vs 청산FX) 모두 반영.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

STOP = -0.03
VOLSPIKE = 2.5
MAX_HOLD = 40
COST = (1.5 + 5.0) / 10000
MIN_TV_USD = 20e6
MAX_US = 5                 # 동시 보유 US 종목 상한(공유풀이라 KR 여지 남김)
ALLOC_PCT = 0.10          # 종목당 시드의 10%(50만원)
_STATE = "v1_us_state.json"
_SHARED = "open_positions.json"


def _emas(df):
    c = df["close"].astype(float)
    return dict(
        close=c.to_numpy(float), open=df["open"].astype(float).to_numpy(float),
        vol=df["volume"].astype(float).to_numpy(float),
        ma5=c.rolling(5, min_periods=1).mean().to_numpy(float),
        ma20=c.rolling(20, min_periods=1).mean().to_numpy(float),
        ma60=c.rolling(60, min_periods=1).mean().to_numpy(float),
        va20=df["volume"].astype(float).rolling(20, min_periods=5).mean().to_numpy(float),
        dates=[d.strftime("%Y-%m-%d") for d in df.index],
    )


def exit_today(a, j, entry_usd, bars_held):
    """v7 청산(1일 판정) — 5일선 이탈/대량음봉/−3%손절/40일. → (청산?, 수익률, 사유)."""
    r = (a["close"][j] - entry_usd) / entry_usd
    if r <= STOP:
        return True, STOP, "손절 −3%"
    if a["close"][j] < a["ma5"][j]:
        return True, r, "5일선 이탈"
    if a["close"][j] < a["open"][j] and a["vol"][j] >= a["va20"][j] * VOLSPIKE:
        return True, r, "대량거래량 음봉"
    if bars_held >= MAX_HOLD:
        return True, r, "최대 보유(40일)"
    return False, r, ""


def _bar_index(a, d):
    try:
        return a["dates"].index(d)
    except ValueError:
        return None


def load_state(state_dir):
    p = Path(state_dir) / _STATE
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"asOf": "", "realized_krw": 0.0, "open": [], "trades": [], "equity_hist": []}


def save_state(state_dir, st):
    st["trades"] = st["trades"][-400:]; st["equity_hist"] = st["equity_hist"][-400:]
    (Path(state_dir) / _STATE).write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _shared_cash(state_dir):
    """중/단기 스윙과 공유하는 현금(open_positions.json) 로드 → (cash, 원본dict)."""
    p = Path(state_dir) / _SHARED
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return float(d.get("cash", 0.0)), d
    except (OSError, json.JSONDecodeError):
        return 0.0, {"cash": 0.0, "holdingsValue": 0.0, "positions": []}


def _write_shared_cash(state_dir, raw, cash):
    """cash만 갱신해 되쓰기 — KR 포지션 보존."""
    raw["cash"] = round(cash, 2)
    (Path(state_dir) / _SHARED).write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_us_universe(cfg):
    """S&P500 ∪ 관심종목(US) 일봉 — crosses 캐시 재사용·갱신."""
    import pickle
    import FinanceDataReader as fdr
    from ..market import crosses as _CR
    from ..obsidian.reader import VaultReader
    p = Path(cfg.state_dir) / _CR._US_CACHE
    us = {}
    if p.exists():
        try:
            us = pickle.load(open(p, "rb"))
        except Exception:  # noqa: BLE001
            us = {}
    codes = [str(r["Symbol"]) for _, r in fdr.StockListing("S&P500").iterrows()]
    try:
        wl = [str(n.ticker) for n in VaultReader(cfg).stock_notes()
              if getattr(n, "ticker", None) and not str(n.ticker)[:1].isdigit()]
        codes += [c for c in wl if c not in codes]
    except Exception:  # noqa: BLE001
        pass
    us = _CR._refresh(us, codes, (date.today() - timedelta(days=620)).isoformat())
    pickle.dump(us, open(p, "wb"))
    return {k: v for k, v in us.items() if v is not None}


def run_v1_us(cfg) -> dict:
    """V1 US 1사이클(미국 종가 후) — 원화·공유현금·환율. 보유 청산 → 신규 진입(모멘텀 랭킹, MOC)."""
    from ..notify.discord import notify_embeds
    from ..obsidian.writer import VaultWriter
    from ..market.fx import get_usdkrw
    from ..state import daily_marker as _DM
    seed = float(cfg.get("capital", "seed", default=5_000_000))
    fx = get_usdkrw()
    panel = _load_us_universe(cfg)
    arrs = {tk: _emas(df) for tk, df in panel.items() if df is not None and len(df) >= 65}
    if not arrs:
        return {"exited": 0, "entered": 0, "held": 0, "skipped": "패널 없음"}
    d = max(a["dates"][-1] for a in arrs.values())
    st = load_state(cfg.state_dir)
    cash, shared_raw = _shared_cash(cfg.state_dir)
    exits_done, entries_done = [], []

    if st.get("asOf") != d:
        # (1) 보유 청산 — 확정봉 d, 종가 체결. 손익=종목수익×환율(진입FX↔청산FX)
        still = []
        for pos in st["open"]:
            a = arrs.get(pos["ticker"]); j = _bar_index(a, d) if a else None
            if j is None:
                still.append(pos); continue
            held = pos["bars_held"] + 1
            done, r, why = exit_today(a, j, pos["entry_usd"], held)
            if done:
                cost_krw = pos["qty"] * pos["entry_usd"] * pos["fx_entry"]
                proceeds_krw = pos["qty"] * pos["entry_usd"] * (1 + r - COST) * fx
                cash += proceeds_krw
                pnl_krw = proceeds_krw - cost_krw
                st["realized_krw"] += pnl_krw
                st["trades"].append({"ticker": pos["ticker"], "entry_date": pos["entry_date"], "exit_date": d,
                                     "ret_pct": round(r * 100, 2), "pnl_krw": round(pnl_krw), "reason": why})
                exits_done.append((pos["ticker"], round(r * 100, 2), round(pnl_krw), why))
            else:
                pos["bars_held"] = held; still.append(pos)
        st["open"] = still
        # (2) 신규 진입 — 확정봉 d, 모멘텀 랭킹, 원화 사이징(공유현금 여유만큼)
        held_t = {p["ticker"] for p in st["open"]}
        slots = MAX_US - len(st["open"])
        alloc = seed * ALLOC_PCT
        if slots > 0:
            cands = []
            for tk, a in arrs.items():
                i = _bar_index(a, d)
                if i is None or i < 60 or tk in held_t:
                    continue
                up = a["close"][i] > a["ma60"][i] and a["ma20"][i] > a["ma60"][i]
                pull = a["close"][i] <= a["ma20"][i] * 1.01 and a["close"][i] > a["close"][i - 1]
                if not (up and pull and a["close"][i] * a["vol"][i] >= MIN_TV_USD):
                    continue
                cands.append((a["close"][i] / a["ma60"][i] - 1.0, tk, float(a["close"][i])))
            cands.sort(key=lambda x: x[0], reverse=True)
            for mom, tk, px_usd in cands:
                if slots <= 0:
                    break
                px_krw = px_usd * fx
                qty = int(min(alloc, cash) // px_krw)
                if qty < 1:
                    continue
                cost_krw = qty * px_krw
                cash -= cost_krw
                st["open"].append({"ticker": tk, "entry_date": d, "entry_usd": px_usd,
                                   "fx_entry": fx, "qty": qty, "bars_held": 0})
                entries_done.append((tk, round(mom * 100, 1), round(cost_krw))); slots -= 1
        # (3) 공유현금 되쓰기 + US 상태 저장
        _write_shared_cash(cfg.state_dir, shared_raw, cash)
        us_hold = sum(p["qty"] * p["entry_usd"] * fx for p in st["open"])
        st["equity_hist"].append({"date": d, "equity": round(st["realized_krw"] + us_hold)})
        st["asOf"] = d
        save_state(cfg.state_dir, st)

    # (4) 브리핑(디스코드 📈 + 옵시디언) — 원화
    us_hold = sum(p["qty"] * p["entry_usd"] * fx for p in st["open"])
    ex_lines = [f"  · {t} {r:+.1f}% ({p:+,}원 · {w})" for t, r, p, w in exits_done] or ["  · 없음"]
    en_lines = [f"  · {t} (모멘텀 +{m}% · {c:,}원)" for t, m, c in entries_done] or ["  · 없음"]
    op_lines = [f"  · {p['ticker']} {p['qty']}주 · 진입 ${p['entry_usd']:,.2f} · {p['bars_held']}일" for p in st["open"][:15]] or ["  · 없음"]
    fields = [{"name": f"📤 청산 {len(exits_done)}건", "value": "\n".join(ex_lines)[:1024], "inline": False},
              {"name": f"📥 신규 진입 {len(entries_done)}건", "value": "\n".join(en_lines)[:1024], "inline": False},
              {"name": f"📊 보유 {len(st['open'])}종목 · 평가 {round(us_hold):,}원", "value": "\n".join(op_lines)[:1024], "inline": False}]
    embed = {"title": f"📈 스윙 V1 US · {d}", "color": 0x2ECC71, "fields": fields,
             "footer": {"text": f"v7 추세추종·US 시장스캔(모멘텀) · 현금 중/단기스윙과 공유 · 실현 {round(st['realized_krw']):,}원 · 환율 {fx:,.0f}"}}
    md = (f"### 📈 스윙 V1 US · {d}\n> v7 추세추종을 US 시장스캔에 적용 · 현금은 중/단기 스윙 500만 풀과 공유 · 환율 {fx:,.0f}\n"
          f"**청산 {len(exits_done)}건**\n" + "\n".join(ex_lines) +
          f"\n\n**신규 진입 {len(entries_done)}건**\n" + "\n".join(en_lines) +
          f"\n\n**보유 {len(st['open'])} · 평가 {round(us_hold):,}원 · 실현누적 {round(st['realized_krw']):,}원**\n" + "\n".join(op_lines) + "\n")
    wh = getattr(cfg.creds, "swing_webhook", None) or getattr(cfg.creds, "scalp_webhook", None)
    notify_embeds(wh, [embed], md)
    VaultWriter(cfg).append_swing_us(md)
    _DM.record_done(cfg.state_dir, "swing_v1_us", datetime.now(_DM.KST))
    return {"exited": len(exits_done), "entered": len(entries_done), "held": len(st["open"]),
            "realized_krw": round(st["realized_krw"])}
