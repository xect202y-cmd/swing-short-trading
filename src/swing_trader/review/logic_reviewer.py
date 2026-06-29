"""AI 로직 진단(logic-review) — 매매일지·의사결정로그·성과를 LLM에 주고 로직 오류+수정안 도출.

피드백 루프의 핵심 엔진: 폴더 데이터를 근거로 'AI가 로직 문제점을 찾아 구체적 config 수정안'을 제안.
사용자가 검토 → config 수정 → `logic` 명령으로 A/B 검증 → 재반영. 키 없으면 룰 기반 요약만.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime

from ..strategy import logic_version as LV
from ..strategy.ai_judge import chat_json
from . import analytics as A

_SYS = (
    "너는 단기 스윙 트레이딩 시스템의 퀀트 로직 감사관이다. 사용자(이용수)는 롱온리 추세추종 스윙 트레이더이고 "
    "목표는 40세 전 순자산 50억, 손절 철저·분할·확률기반이다. 아래 '실제 매매 데이터·의사결정 로그·성과지표'를 "
    "근거로 현재 로직의 문제점과 구체적 수정안을 도출하라. 추측 금지(데이터에 있는 사실만). 표본이 적으면 무리한 결론 금지. "
    "반드시 JSON만 출력: "
    '{"suggestions":[{"title":"짧은 제목","insight":"평이한 근거 1~2문장(한국어)",'
    '"config_key":"점표기 config 키 또는 null","current":현재값_또는_null,"suggested":제안값_또는_null}],'
    '"next_action":"한 줄 권장 액션(한국어)"}'
)


def _aggregate_decisions(state_dir) -> dict:
    ddir = state_dir / "decision_log"
    files = sorted(ddir.glob("*.json")) if ddir.exists() else []
    cands = []
    for f in files[-40:]:                       # 최근 파일들
        try:
            cands += json.loads(f.read_text(encoding="utf-8")).get("candidates", [])
        except (OSError, json.JSONDecodeError):
            continue
    kinds = Counter(c.get("kind") for c in cands)
    entry_types = Counter((c.get("rationale") or {}).get("entry_type") for c in cands if c.get("kind") == "BUY")
    methods = Counter(m for c in cands if c.get("kind") == "BUY" for m in (c.get("methods") or []))
    blocks = Counter((c.get("block_reasons") or ["-"])[0] for c in cands if c.get("kind") == "BLOCKED")
    tmethods = Counter(c.get("target_method_used") for c in cands if c.get("kind") == "BUY")
    return {
        "candidates": len(cands), "kinds": dict(kinds),
        "buy_entry_types": dict(entry_types.most_common(5)),
        "buy_methods": dict(methods.most_common(5)),
        "block_reasons": dict(blocks.most_common(6)),
        "target_methods": dict(tmethods),
    }


def _evidence_text(cfg) -> tuple[str, A.Metrics, dict]:
    seed = float(cfg.get("capital", "seed", default=5_000_000))
    closed = A.load_closed(cfg.state_dir)
    equity = A.load_equity(cfg.state_dir)
    m = A.compute_metrics(closed, equity, seed)
    comp = A.compound_projection(equity, seed)
    agg = _aggregate_decisions(cfg.state_dir)
    snap = LV.snapshot(cfg)
    lines = [
        "[성과지표]",
        f"- 청산 {m.n_closed}건 · 승률 {m.win_rate}% · PF {m.profit_factor} · 평균수익 {m.avg_win_pct}% · 평균손실 {m.avg_loss_pct}%",
        f"- 누적수익율 {m.return_pct}% · MDD {m.max_drawdown_pct}% · 평균보유 {m.avg_hold_days}일 · 손절준수 {m.rule_adherence}%",
        f"- 목표달성 {m.target_hit} · 손절 {m.stop_hit}",
        f"- 점수대별 승률: {m.by_score_band}",
    ]
    if comp and comp.get("annual_pct") is not None:
        lines.append(f"- 복리추정(현재 페이스): 연 {comp['annual_pct']}% (표본 {comp['days']}일)")
    lines += [
        "\n[의사결정 로그 집계(최근)]",
        f"- 후보 {agg['candidates']}개 · 분포 {agg['kinds']}",
        f"- BUY 진입유형 {agg['buy_entry_types']}",
        f"- BUY 부합기법 {agg['buy_methods']}",
        f"- 차단사유 상위 {agg['block_reasons']}",
        f"- 목표/손절 방식 {agg['target_methods']}",
        "\n[현재 로직 설정(config)]",
        json.dumps(snap, ensure_ascii=False),
    ]
    return "\n".join(lines), m, agg


def build_review(cfg) -> tuple[dict, str]:
    """(state, evidence). state는 대시보드/MD 공용 단일 소스."""
    d = date.today().isoformat()
    evidence, m, agg = _evidence_text(cfg)
    if m.n_closed < 3:
        return {"ok": False, "date": d, "n_closed": m.n_closed,
                "reason": "청산 3건 미만 — 데이터 축적 중"}, evidence
    user = f"{evidence}\n\n위 데이터로 현재 스윙 로직의 문제점과 구체적 config 수정안을 진단해줘."
    ai = chat_json(cfg, _SYS, user)
    if not ai or not isinstance(ai.get("suggestions"), list):
        return {"ok": False, "date": d, "n_closed": m.n_closed,
                "reason": "AI 키 없음 또는 응답 파싱 실패"}, evidence
    return {
        "ok": True, "date": d, "n_closed": m.n_closed,
        "headline": {"win_rate": m.win_rate, "profit_factor": m.profit_factor,
                     "return_pct": m.return_pct},
        "suggestions": ai["suggestions"],
        "next_action": str(ai.get("next_action", "")),
    }, evidence


def render_md(state: dict, evidence: str) -> str:
    d = state.get("date", date.today().isoformat())
    fm = (f"---\ntype: 스윙AI로직진단\n날짜: {d}\ntags: [스윙, 로직, AI진단]\n---\n"
          f"> 생성 {datetime.now():%Y-%m-%d %H:%M}\n\n")
    if not state.get("ok"):
        body = (f"## 🤖 AI 로직 진단 · {d}\n{state.get('reason', '진단 보류')}.\n\n"
                f"**근거 데이터**\n```\n{evidence}\n```")
        return fm + body
    lines = [f"## 🤖 AI 로직 진단 · {d}\n"]
    for i, s in enumerate(state["suggestions"], 1):
        cfg_line = ""
        if s.get("config_key"):
            cfg_line = f" · config `{s['config_key']}` 현재값 {s.get('current')} → 제안값 {s.get('suggested')}"
        lines.append(f"{i}. **{s.get('title', '')}**{cfg_line}\n   - {s.get('insight', '')}")
    lines.append(f"\n**다음 액션**: {state.get('next_action', '')}")
    body = "\n".join(lines)
    return (fm + body
            + f"\n\n---\n<details><summary>근거 데이터</summary>\n\n```\n{evidence}\n```\n</details>\n")


def render_discord(state: dict) -> str | None:
    if not state.get("ok"):
        return None
    titles = " · ".join(s.get("title", "") for s in state["suggestions"][:4])
    return f"🧠 **스윙 AI 로직 진단**\n{titles}\n다음 액션: {state.get('next_action', '')}"[:1800]


def run(cfg) -> tuple[str | None, str, dict]:
    """(discord, md, state). 키 없으면 discord=None."""
    state, evidence = build_review(cfg)
    return render_discord(state), render_md(state, evidence), state
