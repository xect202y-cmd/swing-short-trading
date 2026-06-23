"""매매 기법 온톨로지 연동 — 네 볼트의 기법 '신호규칙'을 실데이터로 평가해 매칭.

methods_signals.json (compile_methods.py 산출)의 각 기법은 '신호규칙' 식을 가진다.
  예: "close > ma20 & ma20 > ma60 & vol < vol_ma20 & low <= ma20 * 1.02"
변수명이 제각각이라 정규화 후, 일봉으로 평가 가능한 기법만 매칭한다.
(분봉/포지션/주관 변수: time·avg_vol_5min·support_level·buy_price·sell·if → skip)
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_PATHS = [
    Path(r"C:\Users\xect2\obsidian-automation\methods_signals.json"),
]
_UNAVAILABLE = ("support_level", "buy_price", "avg_vol_5min", "time")


def _high_max_prior(df, n: int) -> float:
    prior = df["high"].iloc[:-1].tail(n)          # 당일 제외(돌파=신고가 의미)
    return float(prior.max()) if len(prior) else float(df["high"].max())


def _vol_mean(df, n: int) -> float:
    return float(df["volume"].tail(n).mean())


def _normalize(rule: str, df) -> str | None:
    """신호규칙 → 평가 가능한 파이썬 식. 평가 불가면 None."""
    r = rule
    if "sell" in r.lower() or re.search(r"\bif\b", r):       # 매도/조건문 → 진입 매칭 대상 아님
        return None
    r = r.replace("&", " and ").replace("|", " or ")
    r = r.replace("price_retrace_to_ma20", "(low <= ma20*1.02)")
    r = re.sub(r"highest\(\s*high\s*,\s*(\d+)\s*\)", lambda m: repr(_high_max_prior(df, int(m.group(1)))), r)
    r = re.sub(r"(?:sma|ma)\(\s*volume\s*,\s*(\d+)\s*\)", lambda m: repr(_vol_mean(df, int(m.group(1)))), r)
    for alias in ("ma_vol_20", "vol_avg20", "vol_ma20"):
        r = re.sub(rf"\b{alias}\b", "VOL_MA20", r)
    r = re.sub(r"\bprice\b", "close", r)
    if any(re.search(rf"\b{re.escape(u)}\b", r) for u in _UNAVAILABLE):
        return None
    return r


def _context(df, snap) -> dict:
    last = df.iloc[-1]
    return {
        "close": snap.price, "open": float(last["open"]), "high": float(last["high"]),
        "low": float(last["low"]), "vol": snap.vol_today, "volume": snap.vol_today,
        "VOL_MA20": snap.vol_avg.get(20, snap.vol_today),
        "ma5": snap.ma.get(5), "ma20": snap.ma.get(20), "ma60": snap.ma.get(60),
        "rsi": snap.rsi, "prev_high": snap.prev_high,
    }


class MethodMatcher:
    def __init__(self, methods: list[dict]):
        self.methods = methods

    def match(self, df, snap) -> list[str]:
        """현재 데이터에 부합하는 기법명 리스트."""
        if not self.methods or df is None or len(df) < 20:
            return []
        ctx = _context(df, snap)
        if any(v is None for v in (ctx["ma20"], ctx["ma60"])):
            return []
        out = []
        for g in self.methods:
            expr = _normalize(g.get("신호규칙", ""), df)
            if not expr:
                continue
            try:
                if eval(expr, {"__builtins__": {}}, ctx):   # noqa: S307 — 신뢰된 볼트 데이터, builtins 차단
                    out.append(g.get("기법명", "?"))
            except Exception:  # noqa: BLE001 — 평가 불가 규칙은 조용히 skip
                continue
        return out

    def __len__(self) -> int:
        return len(self.methods)


def load_method_matcher(cfg) -> MethodMatcher:
    paths = []
    custom = cfg.get("methods", "signals_path", default=None)
    if custom:
        paths.append(Path(custom))
    paths += _DEFAULT_PATHS
    for p in paths:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                methods = data.get("기법", []) if isinstance(data, dict) else data
                log.info("기법 온톨로지 로드: %d개 (%s)", len(methods), p.name)
                return MethodMatcher(methods)
            except (OSError, json.JSONDecodeError) as e:
                log.warning("기법 파일 로드 실패(%s): %s", p, e)
    log.info("기법 온톨로지 없음 — 기법 매칭 비활성(퀀트 점수만)")
    return MethodMatcher([])
