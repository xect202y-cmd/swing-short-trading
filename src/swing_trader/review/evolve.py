"""자가개선 튜닝 루프 오케스트레이터 — 제안→심판(harness)→사람승인 게이트.

evaluate : 제안 생성→T1 백테 A/B→개선이면 pending 등록+Discord, 악화면 학습원장 기각기록.
adopt/reject : 사람 승인/거절(버전 적용·학습 기록).
"""
from __future__ import annotations

from ..config import load_config
from ..models import now_kst
from ..notify.discord import notify
from ..state.daily_marker import today_kst
from ..strategy import harness as HN
from ..strategy import logic_version as LV
from ..strategy.config_writer import set_config_value
from . import logic_reviewer as LR
from . import proposals as P
from .learning_log import LearningLog


def evaluate(cfg, provider, notes, days) -> dict:
    review, _ev = LR.build_review(cfg)
    if not review.get("ok"):
        return {"ok": False, "reason": review.get("reason", "제안 없음"),
                "proposed": [], "rejected": [], "t2": [], "sent": False}
    ll = LearningLog(cfg.state_dir)
    d = today_kst().isoformat()
    proposed, rejected, t2 = [], [], []
    for s in review["suggestions"]:
        key = s.get("config_key")
        if not key or s.get("suggested") is None or s.get("current") is None:
            continue
        if P.classify(key) == "T2":
            t2.append(s)
            continue
        dirn = P.direction(s["current"], s["suggested"])
        if f"reject:{key}:{dirn}" in ll.rules:      # 이미 기각 학습됨 → 재제안·재백테 금지
            continue
        ab = HN.compare(cfg, provider, notes, days,
                        baseline={}, candidate=P.candidate_params(key, s["suggested"]))
        if ab.verdict == "improve":
            pid = P.proposal_id(d, key, s["suggested"])
            prop = {
                "id": pid, "created": now_kst().isoformat(timespec="seconds"),
                "config_key": key, "current": s["current"], "suggested": s["suggested"],
                "tier": "T1", "title": s.get("title", ""), "insight": s.get("insight", ""),
                "verdict": ab.verdict,
                "oos": {"base_expectancy": ab.base_oos.expectancy,
                        "cand_expectancy": ab.cand_oos.expectancy, "n_oos": ab.n_oos,
                        "base_sharpe": ab.base_oos.sharpe, "cand_sharpe": ab.cand_oos.sharpe},
                "status": "pending",
            }
            P.upsert(cfg.state_dir, prop)
            proposed.append(prop)
        elif ab.verdict in ("worse", "neutral"):
            ll.learn(f"reject:{key}:{dirn}",
                     f"{key} {dirn} 방향은 OOS {ab.verdict}"
                     f"(기대값 {ab.base_oos.expectancy}→{ab.cand_oos.expectancy})", d)
            rejected.append({"config_key": key, "verdict": ab.verdict})
        # insufficient → 보류(학습 안 함, 다음 런 재시도)
    ll.save()
    sent = _notify(cfg, proposed, t2)
    return {"ok": True, "proposed": proposed, "rejected": rejected, "t2": t2, "sent": sent}


def _notify(cfg, proposed, t2) -> bool:
    if not proposed and not t2:
        return False
    lines = []
    for p in proposed:
        o = p["oos"]
        delta = (round(o["cand_expectancy"] - o["base_expectancy"], 3)
                 if o["cand_expectancy"] is not None and o["base_expectancy"] is not None else None)
        lines.append(
            f"🧠 제안 #{p['id']} `{p['config_key']}` {p['current']}→{p['suggested']}\n"
            f"   OOS 기대값 {o['base_expectancy']}→{o['cand_expectancy']}"
            + (f" ({delta:+g}%p)" if delta is not None else "")
            + f" · 과적합 가드 통과 ✅ (OOS {o['n_oos']}건)\n"
            f"   적용: swing adopt {p['id']}  ·  거절: swing reject {p['id']}")
    for s in t2:
        lines.append(f"👀 관찰필요 `{s.get('config_key')}` {s.get('current')}→{s.get('suggested')} "
                     f"— 정량검증 불가(페이퍼 관찰)")
    return notify(cfg.creds.discord_webhook_url, "**🔁 스윙 자가개선 제안**\n" + "\n".join(lines))


def adopt(cfg, pid, config_path) -> dict:
    prop = P.find(cfg.state_dir, pid)
    if not prop:
        return {"ok": False, "reason": f"제안 #{pid} 없음"}
    if prop.get("status") != "pending":
        return {"ok": False, "reason": f"제안 #{pid} 이미 {prop.get('status')}"}
    set_config_value(config_path, prop["config_key"], prop["suggested"],
                     expected_current=prop["current"])
    new_cfg = load_config(str(config_path))
    note = (f"자가개선 채택 #{pid}: {prop['config_key']} "
            f"{prop['current']}→{prop['suggested']} (OOS 검증)")
    vnum = LV.save_version(cfg.state_dir, LV.snapshot(new_cfg), note)
    ll = LearningLog(cfg.state_dir)
    ll.learn(f"accept:{prop['config_key']}:{P.direction(prop['current'], prop['suggested'])}",
             f"{prop['config_key']} {prop['current']}→{prop['suggested']} OOS개선 검증 후 채택",
             today_kst().isoformat())
    ll.save()
    P.set_status(cfg.state_dir, pid, "adopted")
    notify(cfg.creds.discord_webhook_url, f"✅ 제안 #{pid} 채택 — {note} → 로직 v{vnum}")
    return {"ok": True, "version": vnum, "note": note}


def reject(cfg, pid) -> dict:
    prop = P.find(cfg.state_dir, pid)
    if not prop:
        return {"ok": False, "reason": f"제안 #{pid} 없음"}
    P.set_status(cfg.state_dir, pid, "rejected")
    ll = LearningLog(cfg.state_dir)
    ll.learn(f"reject:{prop['config_key']}:{P.direction(prop['current'], prop['suggested'])}",
             f"{prop['config_key']} {prop['current']}→{prop['suggested']} 사람이 거절",
             today_kst().isoformat())
    ll.save()
    notify(cfg.creds.discord_webhook_url, f"🚫 제안 #{pid} 거절 기록")
    return {"ok": True}
