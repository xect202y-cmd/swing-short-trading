"""v6 '오버나잇 상따 + 코스닥 국면게이트' 라이브 — 15시 장중 스캔 런 (매 거래일 15:05 KST 예약 실행).

v6 = v5(폭등 종가매수→익일 시가) + 코스닥(KQ11) 50일선 국면 게이트. 코스닥이 50일선 아래면
신규 진입 보류(정산·청산은 유지). 백테스트: 무필터 PF 1.19→v6 PF 1.27, 위험조정 2.69→4.09,
IS·OOS 양쪽 개선(2026-07-08 검증, MA 30~60 견고). 손대는 건 '진입 게이트'뿐 — 나머지는 v5와 동일.


사이클:
  1) 전일 v5 계획 정산 — 진입=전일 확정 종가(상한가 잠금 마감이면 미체결),
     청산=다음 거래일 확정 시가. ScalpState("v5") 가상계좌에 반영(대시보드 /api/scalp).
  2) 오늘 스캔 — FDR 전시장 스냅샷(현재가·등락률·거래대금)에서 상따 셋업 선별 후
     후보별 일봉 이력으로 거래량 배수·60일 신고가 확인 → 등락률 1등(대장주) 순 상한 2,
     '오늘 종가(동시호가) 매수' 계획 저장.
  3) 브리핑 — 디스코드 ⚡ + 옵시디언(Scalp 노트).

사실/판단 분리: 계획 수량은 스캔 시점 현재가, 정산 손익은 확정 일봉만 사용.
비거래일(주말·휴장)은 스캔을 건너뛴다(삼성전자 당일 일봉 존재로 판정).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .strategy import V5_HIGH_N, V5_LIMIT_CAP, V5_SURGE, V5_VOL_X

_PLAN_FILE = "scalp_v5_plan.json"
SCAN_CAP = 2          # '오직 대장주만' — 등락률 상위 최대 2종목
SCAN_LIMIT_GUARD = 29.0   # 스캔 시점 이 이상은 상한가 잠금 임박 → 종가 체결 불확실, 제외


@dataclass(frozen=True)
class V5Item:
    ticker: str
    name: str
    qty: int
    ref_price: float      # 스캔 시점 현재가(수량 산정용) — 정산은 확정 종가
    chg_pct: float        # 스캔 시점 등락률
    why: str = ""


def pick_candidates(snapshot, hist_fn, min_tv_eok: float = 50.0,
                    today: date | None = None, cap: int = SCAN_CAP) -> list[dict]:
    """전시장 스냅샷 → v5 셋업 후보(대장주 순).

    snapshot: FDR StockListing('KRX') 형태(Code/Name/Market/Close/ChagesRatio/Volume/Amount)
    hist_fn(code) -> 일봉 DataFrame(open..volume) 또는 None. 오늘 봉이 섞여 있으면 제외하고 판정.
    """
    out: list[dict] = []
    pre = snapshot[(snapshot["ChagesRatio"] >= V5_SURGE)
                   & (snapshot["ChagesRatio"] < SCAN_LIMIT_GUARD)
                   & (snapshot["Amount"] >= min_tv_eok * 1e8)]
    pre = pre.sort_values("ChagesRatio", ascending=False)
    for _, r in pre.iterrows():
        code, name, mk = str(r["Code"]), str(r["Name"]), str(r["Market"])
        if not code.endswith("0") or mk not in ("KOSPI", "KOSDAQ") or "스팩" in name:
            continue
        hist = hist_fn(code)
        if hist is None or len(hist) < V5_HIGH_N + 5:
            continue
        if today is not None and hasattr(hist.index, "date"):
            hist = hist[[d < today for d in hist.index.date]]   # 장중엔 오늘 봉이 섞임 → 전일까지
        if len(hist) < V5_HIGH_N + 5:
            continue
        vmean = float(hist["volume"].tail(20).mean())
        if vmean <= 0 or float(r["Volume"]) < V5_VOL_X * vmean:
            continue    # 거래량 폭발 아님(15시 기준 — 종가까진 더 늘어나므로 보수 판정)
        if float(r["Close"]) < float(hist["close"].tail(V5_HIGH_N).max()):
            continue    # 60일 신고가 아님(왼쪽 매물대 존재)
        out.append({"ticker": code, "name": name, "price": float(r["Close"]),
                    "chg_pct": round(float(r["ChagesRatio"]), 2),
                    "vol_x": round(float(r["Volume"]) / vmean, 1),
                    "tv_eok": round(float(r["Amount"]) / 1e8, 0)})
        if len(out) >= cap:
            break
    return out


def settle_items(items: list[V5Item], bars_fn, plan_date: str, cost: float):
    """확정 일봉으로 v5 오버나잇 정산 — (rows, pnl, missing).

    진입 = plan_date 종가(그날 등락률 ≥ V5_LIMIT_CAP 이면 상한가 잠금 → 미체결),
    청산 = plan_date 다음 거래일 시가. 둘 중 하나라도 미확보면 missing(보류).
    """
    rows, pnl, missing = [], 0.0, []
    for it in items:
        df = bars_fn(it.ticker)
        if df is None or len(df) < 2:
            missing.append(it.ticker)
            continue
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        if plan_date not in dates:
            missing.append(it.ticker)
            continue
        i = dates.index(plan_date)
        if i + 1 >= len(df):
            missing.append(it.ticker)   # 다음 거래일 시가 미도착
            continue
        d_bar, n_bar = df.iloc[i], df.iloc[i + 1]
        close_d = float(d_bar["close"])
        prev_c = float(df.iloc[i - 1]["close"]) if i >= 1 else 0.0
        chg = (close_d / prev_c - 1) * 100 if prev_c > 0 else 0.0
        if chg >= V5_LIMIT_CAP:
            rows.append({"ticker": it.ticker, "name": it.name, "qty": it.qty,
                         "entry": None, "exit": None, "pnl": 0.0, "ret_pct": 0.0,
                         "reason": "미체결(상한가 잠금 마감)", "why": it.why})
            continue
        entry = close_d * (1 + cost)
        exit_px = float(n_bar["open"]) * (1 - cost)
        p = (exit_px - entry) * it.qty
        pnl += p
        rows.append({"ticker": it.ticker, "name": it.name, "qty": it.qty,
                     "entry": round(entry, 2), "exit": round(exit_px, 2),
                     "pnl": round(p, 0), "ret_pct": round((exit_px / entry - 1) * 100, 2),
                     "reason": "익일시가청산", "why": it.why})
    return rows, pnl, missing


def load_plan(state_dir: Path) -> dict | None:
    p = Path(state_dir) / _PLAN_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data["items"] = [V5Item(**it) for it in data.get("items", [])]
        return data
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def save_plan(state_dir: Path, plan_date: str, items: list[V5Item], scanned_at: str) -> None:
    p = Path(state_dir) / _PLAN_FILE
    p.write_text(json.dumps({"date": plan_date, "scanned_at": scanned_at,
                             "items": [asdict(i) for i in items]},
                            ensure_ascii=False, indent=2), encoding="utf-8")


def _fdr_hist(code: str, days: int = 160):
    import FinanceDataReader as fdr
    from datetime import timedelta
    try:
        df = fdr.DataReader(code, (date.today() - timedelta(days=days)).isoformat())
        if df is None or df.empty:
            return None
        return df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                  "Close": "close", "Volume": "volume"})[
            ["open", "high", "low", "close", "volume"]].astype(float)
    except Exception:  # noqa: BLE001
        return None


def is_trading_day(today: date) -> bool:
    """오늘이 KR 거래일인지 — 삼성전자 당일 일봉 존재로 판정(장중엔 당일 봉이 잡힘)."""
    if today.weekday() >= 5:
        return False
    df = _fdr_hist("005930", days=10)
    return df is not None and len(df) > 0 and df.index[-1].date() == today


V6_REGIME_MA = 50   # 코스닥 국면 게이트 이동평균(일) — 백테스트서 30~60 견고, 중앙값 채택


def kosdaq_uptrend(ma: int = V6_REGIME_MA) -> bool | None:
    """v6 국면 게이트 — 코스닥(KQ11) 최근 종가 ≥ ma일 이동평균이면 True(진입 허용).

    False=약세(진입 보류). 데이터 미확보면 None → 페일오픈(진입 허용, 경고 로깅)."""
    import FinanceDataReader as fdr
    try:
        s = fdr.DataReader("KQ11", (date.today() - timedelta(days=ma * 2 + 60)).isoformat())["Close"].astype(float)
        if s is None or len(s) < ma + 1:
            return None
        return bool(s.iloc[-1] >= s.tail(ma).mean())
    except Exception:  # noqa: BLE001
        return None


def run_scalp_v6(cfg) -> dict:
    """v6 오버나잇 상따 + 코스닥 국면게이트 1사이클: 전일 계획 정산 → (코스닥 상승국면이면) 오늘 15시 스캔 → 계획·브리핑."""
    import FinanceDataReader as fdr
    from ..notify.discord import notify_embeds
    from ..obsidian.writer import VaultWriter
    from ..state import daily_marker as _DM
    from .account import ScalpState
    log_lines: list[str] = []
    fee = float(cfg.get("paper", "fee_bps", default=1.5))
    slip = float(cfg.get("paper", "slippage_bps", default=5.0))
    cost = (fee + slip) / 10000
    min_tv = float(cfg.get("scalp", "min_tv_eok", default=50))
    today = _DM.today_kst()
    now_hm = datetime.now(_DM.KST).strftime("%H:%M")
    state = ScalpState.load(cfg.state_dir, ("v6",))

    # 1) 전일 계획 정산(확정 일봉) — 미확보면 보류(다음 런 재시도)
    prev = load_plan(cfg.state_dir)
    settled_rows, settled_date, n_settled = [], "—", 0
    if prev and prev.get("items") and prev["date"] < today.isoformat():
        rows, pnl, missing = settle_items(prev["items"], _fdr_hist, prev["date"], cost)
        if missing:
            log_lines.append(f"정산 보류 — {prev['date']} 확정봉 미도착({', '.join(missing)})")
        else:
            state.apply_day(prev["date"], "kr", {"v6": {"pnl": pnl, "shadow_pnl": 0.0, "trades": rows}})
            state.save(cfg.state_dir)
            settled_rows, settled_date = rows, prev["date"]
            n_settled = len(rows)
            save_plan(cfg.state_dir, prev["date"], [], prev.get("scanned_at", ""))  # 정산 완료 표시

    state.save(cfg.state_dir)   # 첫 런에도 시드 기록 — 대시보드 /api/scalp 가동 표시

    # 2) 오늘 스캔(거래일 + 코스닥 상승국면 + 오늘 계획 미수립일 때만 — 재실행 멱등)
    items: list[V5Item] = []
    scanned = False
    already = prev and prev.get("date") == today.isoformat() and prev.get("items")
    trading = is_trading_day(today)
    regime = kosdaq_uptrend()   # v6 국면 게이트: True=진입허용 / False=약세보류 / None=데이터없음(페일오픈)
    if not trading:
        log_lines.append("비거래일 — 스캔 생략")
    elif already:
        pass   # 오늘 계획 이미 있음 — 멱등
    elif regime is False:
        log_lines.append(f"코스닥 {V6_REGIME_MA}일선 하회(약세 국면) — v6 게이트로 신규 진입 보류")
    else:
        if regime is None:
            log_lines.append("코스닥 국면 데이터 미확보 — 게이트 페일오픈(진입 허용)")
        snapshot = fdr.StockListing("KRX")
        cands = pick_candidates(snapshot, _fdr_hist, min_tv_eok=min_tv, today=today)
        scanned = True
        cash = state.models["v6"]["cash"]
        budget = cash / SCAN_CAP
        for c in cands:
            qty = int(budget // c["price"]) if c["price"] > 0 else 0
            if qty < 1:
                continue
            items.append(V5Item(ticker=c["ticker"], name=c["name"], qty=qty,
                                ref_price=c["price"], chg_pct=c["chg_pct"],
                                why=f"+{c['chg_pct']}% · 거래량 {c['vol_x']}배 · 대금 {c['tv_eok']:,.0f}억 · 60일 신고가"))
        if items:
            save_plan(cfg.state_dir, today.isoformat(), items, now_hm)

    # 3) 브리핑(디스코드 ⚡ + 옵시디언)
    day_pnl = sum(r["pnl"] for r in settled_rows)
    res_lines = ([f"[v6 상따] {n_settled}건 · 일손익 {'+' if day_pnl >= 0 else ''}{round(day_pnl):,}원"]
                 if settled_rows else ["[v6 상따] 정산 없음"])
    for r in settled_rows:
        if r["entry"] is None:
            res_lines.append(f"  · {r['name']} {r['reason']}")
        else:
            sign = "+" if r["pnl"] >= 0 else ""
            res_lines.append(f"  · {r['name']} {r['entry']:,.0f}→{r['exit']:,.0f} "
                             f"{sign}{r['pnl']:,.0f}원({r['ret_pct']:+.1f}%) {r['reason']}")
    plan_lines = ([f"  · {i.name} {i.qty}주 · 현재 {i.ref_price:,.0f}원(+{i.chg_pct}%) · {i.why}" for i in items]
                  or (["(오늘 셋업 없음 — 폭등+거래량+신고가 조건 미충족)"] if scanned else ["(스캔 생략)"]))
    fields = [
        {"name": f"📊 {settled_date} 정산 · 일손익 {'+' if day_pnl >= 0 else ''}{round(day_pnl):,}원",
         "value": "\n".join(res_lines + log_lines)[:1024], "inline": False},
        {"name": f"🗺️ {today.isoformat()} 종가 매수 계획 (스캔 {now_hm})",
         "value": "\n".join(plan_lines)[:1024], "inline": False},
    ]
    embed = {"title": "⚡ 단타 v6 상따 · KR", "color": 0xE67E22, "fields": fields,
             "footer": {"text": f"오버나잇(종가→익일시가) · 코스닥 {V6_REGIME_MA}일선 국면필터 · 가상 300만 · 잔고 {state.models['v6']['cash']:,.0f}원"}}
    md = (f"### ⚡ 단타 v6 상따 · KR · {today.isoformat()}\n"
          f"> v5 + 코스닥 {V6_REGIME_MA}일선 국면게이트\n"
          f"**{settled_date} 정산**\n" + "\n".join(res_lines + log_lines) +
          "\n\n**종가 매수 계획**\n" + "\n".join(plan_lines) + "\n")
    notify_embeds(cfg.creds.scalp_webhook, [embed], md)
    VaultWriter(cfg).append_scalp(md)
    _DM.record_done(cfg.state_dir, "scalp_v6_kr", datetime.now(_DM.KST))
    return {"settled": n_settled, "planned": len(items), "scanned": scanned}


# 하위호환 별칭 — 기존 import/CLI(scalp-v5)·테스트가 계속 동작하도록. v6가 정본.
run_scalp_v5 = run_scalp_v6
