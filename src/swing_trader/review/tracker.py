"""차단·WATCH 종목 사후 성과 추적 — '매수 우선순위 로직이 맞았는지' 검증용.

매일 실행마다 BLOCKED/WATCH 후보를 기록하고, 이후 1·3·5거래일 수익률 + 최고/최저를
추적한다(state/tracking.json). 각 daily 실행 = 1거래일로 카운트(bars).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models import SignalKind
from ..state.daily_marker import today_kst


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_and_update(cfg, provider, signals, last_bar_advanced: bool) -> None:
    path = cfg.state_dir / "tracking.json"
    data = _load(path)
    today = today_kst().isoformat()

    # 1) 기존 추적 갱신(거래일 진행 시 bars+1, 1/3/5일 스냅샷)
    for tk, r in data.items():
        if r.get("closed"):
            continue
        try:
            df, _ = provider.get_ohlcv(tk)
            cur = float(df["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            continue
        ref = r.get("ref_price") or cur
        ret = round((cur - ref) / ref * 100, 2) if ref else 0.0
        r["last_ret"] = ret
        r["max_up"] = max(r.get("max_up", 0.0), ret)
        r["max_down"] = min(r.get("max_down", 0.0), ret)
        if last_bar_advanced and r.get("last_update") != today:
            r["bars"] = r.get("bars", 0) + 1
            r["last_update"] = today
            for d in (1, 3, 5):
                if r["bars"] >= d and f"ret_d{d}" not in r:
                    r[f"ret_d{d}"] = ret
            if r["bars"] >= 5:
                r["closed"] = True

    # 2) 신규 BLOCKED/WATCH 기록(미추적분만)
    for s in signals:
        if s.kind not in (SignalKind.BLOCKED, SignalKind.BUY_WATCH) or not s.rank or not s.ticker:
            continue
        if s.ticker in data and not data[s.ticker].get("closed"):
            continue
        data[s.ticker] = {
            "ticker": s.ticker, "name": s.name, "kind": s.kind.value, "date": today,
            "ref_price": s.price, "score": s.score, "rank": s.rank,
            "target": s.plan.target1 if s.plan else None, "stop": s.plan.stop if s.plan else None,
            "block_reason": (s.blocked_reasons[0] if s.blocked_reasons else None),
            "bars": 0, "max_up": 0.0, "max_down": 0.0, "last_update": today,
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
