"""단타 브리핑 — 디스코드 임베드(오렌지 ⚡, 스윙과 시각 분리) + 옵시디언 md."""
from __future__ import annotations

from .account import SEED_PER_MODEL, ScalpState
from .strategy import PlanItem

ORANGE = 0xE67E22
_MK = {"kr": "KR", "us": "US"}


def _won(v) -> str:
    return "—" if v is None else f"{round(v):,}"


def _results_lines(settled: dict) -> list[str]:
    out: list[str] = []
    for m in ("v1", "v2", "v3"):
        rows = settled.get(m) or []
        tag = {"v1": "v1 돌파", "v2": "v2 갭반등", "v3": "v3 터닝갭"}[m]
        if not rows:
            out.append(f"[{tag}] 체결 없음")
            continue
        day = sum(r["pnl"] for r in rows)
        out.append(f"[{tag}] {len(rows)}건 · 일손익 {'+' if day >= 0 else ''}{_won(day)}원")
        for r in rows:
            sign = "+" if r["pnl"] >= 0 else ""
            out.append(f"  · {r['name']} {_won(r['entry'])}→{_won(r['exit'])} "
                       f"{sign}{_won(r['pnl'])}원({r['ret_pct']:+.1f}%) {r['reason']}")
    return out


def _plan_lines(plan: dict) -> list[str]:
    items = [i for i in plan.get("items", []) if isinstance(i, PlanItem) and not i.shadow] or \
            [i for i in plan.get("items", []) if not getattr(i, "shadow", False)]
    if not items:
        return ["(오늘 계획 없음 — 조건 충족 후보 없음)"]
    out: list[str] = []
    for m in ("v1", "v2"):
        mine = [i for i in items if i.model == m]
        if not mine:
            continue
        out.append("[v1 돌파 — 트리거 터치 시 매수]" if m == "v1" else "[v2 갭반등 — 시가 진입]")
        for i in mine:
            trig = f"트리거 {_won(i.trigger)}원 · " if i.trigger else ""
            out.append(f"  · {i.name} {i.qty}주 · {trig}손절 {i.stop_pct:+.1f}% · {i.why}")
    return out


def scalp_brief(market: str, settled: dict, plan: dict, state: ScalpState,
                settled_date: str) -> tuple[dict, str]:
    mk = _MK.get(market, market.upper())
    scen = plan.get("scenario", {})
    total = sum(state.models[m]["cash"] for m in ("v1", "v2", "v3"))
    res = _results_lines(settled)
    pl = _plan_lines(plan)
    day_total = sum(r["pnl"] for m in ("v1", "v2", "v3") for r in (settled.get(m) or []))
    fields = [
        {"name": f"📊 {settled_date} 결과 · 일손익 {'+' if day_total >= 0 else ''}{_won(day_total)}원",
         "value": "\n".join(res)[:1024], "inline": False},
        {"name": f"🗺️ {plan.get('date')} 계획 · 시나리오 리스크 {scen.get('risk', '—')}",
         "value": ("\n".join(scen.get("notes", [])[:2] + pl))[:1024], "inline": False},
    ]
    embed = {"title": f"⚡ 단타 페이퍼 · {mk}", "color": ORANGE, "fields": fields,
             "footer": {"text": f"가상 {SEED_PER_MODEL // 10000}만 ×2모델 · 당일청산 · "
                                f"합산 {_won(total)}원"}}
    md = (f"### ⚡ 단타 · {mk} · {plan.get('date')}\n"
          f"**{settled_date} 결과**\n" + "\n".join(res) +
          f"\n\n**계획(리스크 {scen.get('risk', '—')})**\n" + "\n".join(pl) + "\n")
    return embed, md
