"""전일 리서치 기반 단타 플랜 — 시나리오(거시+지침로그) → 종목 선정 → PlanItem.

사실/판단 분리: 가격·수량은 실측(실시간가/전일봉)만, 볼트는 선별 가중에만.
그림자 A/B: 시나리오 필터 OFF 리스트(shadow)를 항상 병행 산출해 필터의
부가가치 자체를 검증한다(스펙 3). 백테스트는 기계룰만 쓰므로 이 모듈과 무관.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..macro.regime import assess_macro
from .strategy import V1_K, V1_STOP, V2_STOP, PlanItem

MAX_POS = 5
_PLAN_FILE = "scalp_plan.json"


def build_scenario(cfg, reader) -> dict:
    macro = assess_macro(reader.macro_dashboard(), reader.macro_regime(),
                         vix_caution=float(cfg.get("event_filter", "vix_caution", default=20.0)))
    focus_text = ""
    gdir = cfg.vault_root / "금융뉴스" / "지침로그"
    if gdir.exists():
        evenings = sorted(gdir.glob("*-evening.md"))
        if evenings:
            try:
                focus_text = evenings[-1].read_text(encoding="utf-8")
            except OSError:
                focus_text = ""
    return {"risk": macro.risk.value, "notes": macro.notes, "focus_text": focus_text}


def _rank(cands: list[dict], focus_text: str) -> list[dict]:
    def key(c):
        boost = 1 if (c["name"] and c["name"] in focus_text) else 0
        return (boost, c.get("prev_tv_eok") or 0.0)
    return sorted(cands, key=key, reverse=True)


def _qty(budget: float, ref_price: float) -> int:
    return int(budget // ref_price) if ref_price > 0 else 0


def _items(model: str, cands: list[dict], cash: float, quotes: dict,
           cap: int, shadow: bool) -> list[PlanItem]:
    out: list[PlanItem] = []
    budget = cash / MAX_POS
    for c in cands:
        if len(out) >= cap:
            break
        ref = quotes.get(c["ticker"]) or c["prev_close"]
        q = _qty(budget, ref)
        if q < 1:
            continue
        if model == "v1":
            out.append(PlanItem(model="v1", ticker=c["ticker"], name=c["name"], qty=q,
                                stop_pct=V1_STOP, prev_close=c["prev_close"],
                                prev_range=c["prev_range"], k=V1_K,
                                why=c.get("why", ""), shadow=shadow))
        else:
            out.append(PlanItem(model="v2", ticker=c["ticker"], name=c["name"], qty=q,
                                stop_pct=V2_STOP, prev_close=c["prev_close"],
                                prev_range=c["prev_range"],
                                why=c.get("why", ""), shadow=shadow))
    return out


def build_plan(candidates: list[dict], cash_by_model: dict, scenario: dict,
               quotes: dict) -> dict:
    ranked = _rank(candidates, scenario.get("focus_text", ""))
    base = _rank(candidates, "")                      # 그림자 = 시나리오 무가중
    v1_cap = 2 if scenario.get("risk") == "높음" else MAX_POS
    up = [c for c in ranked if c.get("uptrend")]
    up_base = [c for c in base if c.get("uptrend")]
    return {
        "v1": _items("v1", ranked, cash_by_model["v1"], quotes, v1_cap, False),
        "v2": _items("v2", up, cash_by_model["v2"], quotes, MAX_POS, False),
        "v1_shadow": _items("v1", base, cash_by_model["v1"], quotes, MAX_POS, True),
        "v2_shadow": _items("v2", up_base, cash_by_model["v2"], quotes, MAX_POS, True),
    }


def save_plan(state_dir: Path, market: str, plan: dict) -> None:
    p = Path(state_dir) / _PLAN_FILE
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    items = plan["items"]
    data[market] = {"date": plan["date"], "scenario": plan["scenario"],
                    "items": [asdict(i) if isinstance(i, PlanItem) else i for i in items]}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_plans(state_dir: Path) -> dict:
    p = Path(state_dir) / _PLAN_FILE
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    for mk, entry in data.items():
        entry["items"] = [PlanItem(**it) for it in entry.get("items", [])]
    return data
