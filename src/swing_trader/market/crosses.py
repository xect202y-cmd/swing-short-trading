"""골든/데드 크로스 스캐너 — 50/200일 이평 교차 (KR 전시장 + US S&P500).

Rule(정석 50/200 크로스 + 보강 필터):
- 골든크로스: 전일 MA50 ≤ MA200 이고 당일 MA50 > MA200 (데드크로스는 반대)
- 보강(가점·표기, 검색 Rule 반영): ①당일 거래량 ≥ 20일 평균(세력 유입 확인 — 거래량 없는
  크로스는 신뢰 낮음) ②MA200 기울기 비하락(5일) — 하락 중 크로스는 휩쏘 잦음
  ③종가가 MA50 위(추세 살아있음) ④유동성 하한(거래대금)
- 타점: 매수구간 = MA50~종가(크로스 후 눌림은 50일선 지지에서 매수), 손절 = MA200
  (이탈 시 크로스 무효), 목표 = 종가+15% 와 120일 고점 중 높은 쪽

용도: ①앱 '추천 > 골든크로스' 탭 TOP10(한/미 통합, 타점 포함) ②디스코드 알림 —
골든 ∩ (보유+관심) = "추가 매수/신규 진입 검토", 데드 ∩ 보유 = "대응 필요".
정산·매매 없음(정보 제공). 확정 일봉만 사용(KR=전일 마감, US=직전 세션 마감).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from ..scalp.krx_universe import load_cache as _load_kr, save_cache as _save_kr

_US_CACHE = "us_panel.pkl"
MIN_KR_TV_EOK = 50.0        # KR 거래대금 하한(억원)
MIN_US_DVOL = 2e7           # US 달러 거래대금 하한($20M)
TOP_N = 10


# ── 패널 준비(증분 갱신) ──────────────────────────────────────────────────────
def _fdr_tail(code: str, start: str):
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader(code, start)
        if df is None or df.empty:
            return None
        return df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                  "Close": "close", "Volume": "volume"})[
            ["open", "high", "low", "close", "volume"]].astype(float)
    except Exception:  # noqa: BLE001
        return None


def _refresh(panel: dict, codes: list[str], full_start: str, workers: int = 8) -> dict:
    """캐시 증분 갱신 — 마지막 봉이 3일 이상 낡은 종목만 꼬리 재수집 후 병합."""
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor, as_completed
    stale_cut = date.today() - timedelta(days=3)
    todo: list[tuple[str, str]] = []
    for c in codes:
        df = panel.get(c)
        if c in panel and df is None:
            continue   # 상장폐지 등 실패 마커는 재시도 안 함
        if df is None:
            todo.append((c, full_start))
        elif df.index[-1].date() < stale_cut:
            todo.append((c, (df.index[-1].date() - timedelta(days=7)).isoformat()))
    def one(c, s):
        return c, s, _fdr_tail(c, s)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, c, s) for c, s in todo]
        for fu in as_completed(futs):
            c, s, tail = fu.result()
            if tail is None:
                if c not in panel:
                    panel[c] = None
                continue
            old = panel.get(c)
            if old is not None and s != full_start:
                merged = pd.concat([old[old.index < tail.index[0]], tail])
                panel[c] = merged.tail(500)
            else:
                panel[c] = tail.tail(500)
    return panel


def prepare_panels(state_dir: Path) -> tuple[dict, dict, dict]:
    """KR 전시장 + US S&P500 패널(확정 일봉) 준비. 반환: (kr, us, us_names)."""
    import FinanceDataReader as fdr
    from ..scalp.krx_universe import list_universe
    full_start = (date.today() - timedelta(days=620)).isoformat()
    kr = _load_kr(state_dir)
    kr_codes = [u["code"] for u in list_universe()]
    kr = _refresh(kr, kr_codes, full_start)
    _save_kr(state_dir, kr)

    import pickle
    us_p = Path(state_dir) / _US_CACHE
    us: dict = {}
    if us_p.exists():
        try:
            us = pickle.load(open(us_p, "rb"))
        except Exception:  # noqa: BLE001
            us = {}
    li = fdr.StockListing("S&P500")
    us_names = {str(r["Symbol"]): str(r["Name"]) for _, r in li.iterrows()}
    us = _refresh(us, list(us_names.keys()), full_start)
    pickle.dump(us, open(us_p, "wb"))
    return ({k: v for k, v in kr.items() if v is not None},
            {k: v for k, v in us.items() if v is not None}, us_names)


# ── 크로스 판정 ──────────────────────────────────────────────────────────────
def detect_cross(df) -> dict | None:
    """마지막 확정 봉에서 50/200 크로스 판정 — 없으면 None.

    반환: {kind, close, ma50, ma200, vol_ratio, slope200_pct, above50, high120}
    """
    if df is None or len(df) < 205:
        return None
    c, v = df["close"], df["volume"]
    ma50_t = float(c.iloc[-50:].mean())
    ma200_t = float(c.iloc[-200:].mean())
    ma50_p = float(c.iloc[-51:-1].mean())
    ma200_p = float(c.iloc[-201:-1].mean())
    if ma50_p <= ma200_p and ma50_t > ma200_t:
        kind = "golden"
    elif ma50_p >= ma200_p and ma50_t < ma200_t:
        kind = "dead"
    else:
        return None
    ma200_5ago = float(c.iloc[-205:-5].mean())
    vmean = float(v.iloc[-21:-1].mean())
    return {
        "kind": kind,
        "close": float(c.iloc[-1]),
        "ma50": round(ma50_t, 2), "ma200": round(ma200_t, 2),
        "vol_ratio": round(float(v.iloc[-1]) / vmean, 2) if vmean > 0 else 0.0,
        "slope200_pct": round((ma200_t / ma200_5ago - 1) * 100, 2) if ma200_5ago > 0 else 0.0,
        "above50": bool(float(c.iloc[-1]) > ma50_t),
        "high120": float(df["high"].iloc[-120:].max()),
        "date": df.index[-1].strftime("%Y-%m-%d"),
    }


def _score(m: dict, tv_norm: float) -> float:
    """랭킹 점수 — 보강 필터 충족일수록 높게(거래량 확인이 가장 큰 가중)."""
    s = min(m["vol_ratio"], 3.0) * 2.0
    s += 2.0 if m["slope200_pct"] >= 0 else 0.0
    s += 1.0 if m["above50"] else 0.0
    s += min(tv_norm, 2.0)
    return round(s, 2)


def _entry(m: dict) -> dict:
    """타점 — 매수구간(50일선 지지~현재가)·손절(200일선)·목표(+15% vs 120일 고점)."""
    return {"buyLow": round(m["ma50"], 2), "buyHigh": round(m["close"], 2),
            "stop": round(m["ma200"], 2),
            "target": round(max(m["close"] * 1.15, m["high120"]), 2)}


def scan(kr: dict, us: dict, us_names: dict, kr_names: dict) -> dict:
    golden: list[dict] = []
    dead: list[dict] = []
    for code, df in kr.items():
        m = detect_cross(df)
        if not m:
            continue
        tv_eok = m["close"] * float(df["volume"].iloc[-1]) / 1e8
        if tv_eok < MIN_KR_TV_EOK:
            continue
        item = {"ticker": code, "name": kr_names.get(code, code), "market": "KR",
                **m, "score": _score(m, tv_eok / 500), **_entry(m)}
        (golden if m["kind"] == "golden" else dead).append(item)
    for sym, df in us.items():
        m = detect_cross(df)
        if not m:
            continue
        dvol = m["close"] * float(df["volume"].iloc[-1])
        if dvol < MIN_US_DVOL:
            continue
        item = {"ticker": sym, "name": us_names.get(sym, sym), "market": "US",
                **m, "score": _score(m, dvol / 2e9), **_entry(m)}
        (golden if m["kind"] == "golden" else dead).append(item)
    golden.sort(key=lambda x: x["score"], reverse=True)
    dead.sort(key=lambda x: x["score"], reverse=True)
    return {"golden": golden, "dead": dead}


# ── 보유/관심 매칭 ────────────────────────────────────────────────────────────
def holdings_names(cfg) -> set[str]:
    """볼트 포트폴리오 노트(03_Ontology/이용수/💼 포트폴리오.md)의 [[보유종목]] 이름들."""
    p = cfg.vault_root / "03_Ontology" / "이용수" / "💼 포트폴리오.md"
    if not p.exists():
        return set()
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {m.group(1).strip() for m in re.finditer(r"\[\[([^\]|#]+)", txt)}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def match_names(items: list[dict], names: set[str], name2ticker: dict) -> list[dict]:
    """크로스 항목을 이름/티커로 보유·관심 집합과 매칭(한글명↔티커는 관심종목 노트 사전)."""
    keys = {_norm(n) for n in names}
    tickers = {str(name2ticker.get(n, "")).upper() for n in names if name2ticker.get(n)}
    out = []
    for it in items:
        if _norm(it["name"]) in keys or _norm(it["ticker"]) in keys or it["ticker"].upper() in tickers:
            out.append(it)
    return out


# ── 실행(스캔 → state → 디스코드) ────────────────────────────────────────────
def _push_hermes(hold_dead: list[dict], watch_golden: list[dict]) -> None:
    """헤르메스 대시보드 앱으로 웹푸시 — 보유 데드/보유·관심 골든만. env 없으면 조용히 스킵.
    HERMES_BASE_URL(앱 도메인) + HERMES_PUSH_SECRET(= 앱 PUSH_SEND_SECRET) 필요."""
    import os
    base = os.getenv("HERMES_BASE_URL", "https://hermes-dashboard-five-tau.vercel.app")
    secret = os.getenv("HERMES_PUSH_SECRET", "")
    if not secret or (not hold_dead and not watch_golden):
        return
    import json as _json
    import urllib.request
    def _send(title: str, body: str):
        try:
            req = urllib.request.Request(
                f"{base}/api/push/send",
                data=_json.dumps({"title": title, "body": body, "url": "/picks"}).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}"})
            urllib.request.urlopen(req, timeout=8).read()
        except Exception:  # noqa: BLE001 — 푸시 실패가 파이프라인을 막지 않게
            pass
    if hold_dead:
        names = ", ".join(d["name"] for d in hold_dead[:3])
        _send("⚠️ 보유 종목 데드크로스", f"{names} — 50/200일 데드크로스, 대응 검토")
    if watch_golden:
        names = ", ".join(g["name"] for g in watch_golden[:3])
        _send("✨ 관심 종목 골든크로스", f"{names} — 추가매수·신규진입 검토")


def run_crosses(cfg) -> dict:
    from ..main import VaultReader, _load_notes
    from ..notify.discord import notify_embeds
    from ..scalp.krx_universe import list_universe
    reader = VaultReader(cfg)
    notes = [n for n in _load_notes(cfg, reader, None, "all") if n.ticker]
    watch_names = {n.name or n.ticker for n in notes}
    name2ticker = {(n.name or n.ticker): n.ticker for n in notes}
    holds = holdings_names(cfg)

    kr, us, us_names = prepare_panels(cfg.state_dir)
    kr_names = {u["code"]: u["name"] for u in list_universe()}
    res = scan(kr, us, us_names, kr_names)
    golden, dead = res["golden"], res["dead"]

    watch_golden = match_names(golden, holds | watch_names, name2ticker)
    hold_dead = match_names(dead, holds, name2ticker)
    out = {
        "as_of": date.today().isoformat(),
        "rule": "50/200일 이평 골든크로스 · 보강: 거래량 20일평균↑·200일선 기울기·50일선 위",
        "golden_top": golden[:TOP_N],
        "golden_count": len(golden), "dead_count": len(dead),
        "watch_golden": watch_golden,               # 보유·관심 중 골든 → 추가매수/신규진입 검토
        "hold_dead": hold_dead,                     # 보유 중 데드 → 대응 필요
        "dead_all": [{"ticker": d["ticker"], "name": d["name"], "market": d["market"]} for d in dead],
    }
    (Path(cfg.state_dir) / "crosses.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 헤르메스 앱 푸시 — 보유 데드(대응) 또는 보유·관심 골든(진입)일 때만(전시장 전체는 도배).
    _push_hermes(hold_dead, watch_golden)

    # 디스코드 — 매치가 있거나 골든이 하나라도 있으면 발송(정보성, 매일 도배 방지)
    if golden or hold_dead:
        flag = {"KR": "🇰🇷", "US": "🇺🇸"}
        line = (lambda g: f"{flag[g['market']]} {g['name']} · 종가 {g['close']:,.2f} · 거래량 {g['vol_ratio']}배 "
                          f"· 매수 {g['buyLow']:,.2f}~{g['buyHigh']:,.2f} · 손절 {g['stop']:,.2f} · 목표 {g['target']:,.2f}")
        fields = []
        if hold_dead:
            fields.append({"name": "⚠️ 보유 종목 데드크로스 — 대응 필요",
                           "value": "\n".join(f"{flag[d['market']]} {d['name']} · 종가 {d['close']:,.2f} · 손절선(200일) {d['ma200']:,.2f}" for d in hold_dead)[:1024],
                           "inline": False})
        if watch_golden:
            fields.append({"name": "🌟 보유·관심 골든크로스 — 추가매수/신규진입 검토",
                           "value": "\n".join(line(g) for g in watch_golden[:8])[:1024], "inline": False})
        if golden:
            fields.append({"name": f"📋 오늘 골든크로스 TOP{min(len(golden), 5)} (전시장 KR+US {len(golden)}건)",
                           "value": "\n".join(line(g) for g in golden[:5])[:1024], "inline": False})
        embed = {"title": f"✨ 골든/데드 크로스 · {out['as_of']}", "color": 0xD4AF37,
                 "fields": fields,
                 "footer": {"text": "50/200일 정석 룰 + 거래량·기울기 보강 · KR 전일/US 직전 세션 마감 기준"}}
        notify_embeds(cfg.creds.discord_webhook_url, [embed],
                      f"골든 {len(golden)}건 · 보유 데드 {len(hold_dead)}건")
    return {"golden": len(golden), "dead": len(dead),
            "watch_golden": len(watch_golden), "hold_dead": len(hold_dead)}
