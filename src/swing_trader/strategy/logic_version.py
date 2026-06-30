"""로직 버전 관리 — 점수/리스크/AI 규칙을 스냅샷하고, 변경 시 이전과 비교.

'로직'을 정량 키로 평탄화(flatten)해 버전별로 저장한다(state/logic_versions.json).
변경하면 이전 버전과 diff + A/B 백테스트(익절/손절 규칙)로 승률을 비교하고,
변경 내역을 옵시디언(04_Trading/Logic/)에 기록한다.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path


def snapshot(cfg) -> dict:
    """현재 config 의 '로직' 정량값을 평탄 dict 로."""
    flat: dict = {}
    cap = cfg.get("capital", default={})
    for k in ("seed", "max_positions", "max_per_stock", "first_entry", "weekly_target"):
        flat[f"capital.{k}"] = cap.get(k)
    rk = cfg.get("risk", default={})
    for k in ("default_stop_pct", "max_stop_pct", "take1_pct", "take2_pct", "min_reward_risk",
              "daily_loss_limit", "account_loss_limit", "min_trading_value_eok",
              "momentum_exit_days", "soft_hold_days", "max_hold_days", "partial_exit_pct", "trail_pct",
              "require_uptrend"):
        flat[f"risk.{k}"] = rk.get(k)
    sc = cfg.get("scoring", default={})
    for k, v in (sc.get("weights", {}) or {}).items():
        flat[f"scoring.weights.{k}"] = v
    for k, v in (sc.get("thresholds", {}) or {}).items():
        flat[f"scoring.thresholds.{k}"] = v
    flat["scoring.method_bonus_per"] = sc.get("method_bonus_per")
    flat["scoring.method_bonus_max"] = sc.get("method_bonus_max")
    ai = cfg.get("ai", default={})
    for k in ("enabled", "min_score", "veto_confidence"):
        flat[f"ai.{k}"] = ai.get(k)
    return flat


def diff(old: dict | None, new: dict) -> list[str]:
    if not old:
        return []
    out = []
    for k in sorted(set(old) | set(new)):
        ov, nv = old.get(k), new.get(k)
        if ov != nv:
            out.append(f"`{k}`: {ov} → **{nv}**")
    return out


def _path(state_dir: Path) -> Path:
    return state_dir / "logic_versions.json"


def load_versions(state_dir: Path) -> list[dict]:
    p = _path(state_dir)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_version(state_dir: Path, snap: dict, note: str) -> int:
    versions = load_versions(state_dir)
    vnum = len(versions) + 1
    versions.append({"version": vnum, "date": date.today().isoformat(),
                     "created": datetime.now().isoformat(timespec="seconds"),
                     "note": note, "snapshot": snap})
    state_dir.mkdir(parents=True, exist_ok=True)
    _path(state_dir).write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8")
    return vnum


def render(vnum: int, note: str, changes: list[str], ab: tuple | None, prev_v: int | None,
           runner_ab: tuple | None = None) -> str:
    d = date.today().isoformat()
    lines = [
        "---", "type: 스윙로직변경", f"버전: v{vnum}", f"날짜: {d}", "tags: [스윙, 로직, 변경이력]", "---",
        f"# 🔧 로직 변경 v{vnum} · {d}", f"> 생성 {datetime.now():%Y-%m-%d %H:%M}", "",
        f"**변경 사유**: {note}", "",
    ]
    if prev_v is None:
        lines += ["## 📌 베이스라인", "- 최초 로직 스냅샷(v1). 이후 변경부터 이전 버전과 비교됩니다.", ""]
    else:
        lines.append(f"## 📝 변경 내역 (v{prev_v} → v{vnum})")
        lines += [f"- {c}" for c in changes] if changes else ["- (정량 로직 변경 없음 — 메모만 기록)"]
        lines.append("")
        if ab:
            old_s, new_s = ab
            lines += [
                "## 🧪 A/B 백테스트 (60일 · 익절/손절 규칙 비교)",
                "| 로직 | 종목 | 거래수 | 평균승률 |",
                "|---|---|---|---|",
                f"| 이전 v{prev_v} | {old_s.n_stocks} | {old_s.total_trades} | "
                f"{old_s.avg_win_rate if old_s.avg_win_rate is not None else '—'}% |",
                f"| **현재 v{vnum}** | {new_s.n_stocks} | {new_s.total_trades} | "
                f"**{new_s.avg_win_rate if new_s.avg_win_rate is not None else '—'}%** |",
            ]
            if old_s.avg_win_rate is not None and new_s.avg_win_rate is not None:
                delta = round(new_s.avg_win_rate - old_s.avg_win_rate, 1)
                verdict = "개선 ✅" if delta > 0 else ("악화 ⚠️" if delta < 0 else "동일")
                lines.append(f"\n→ **승률 변화 {delta:+.1f}%p ({verdict})**")
            lines += ["", "> ⚠️ A/B는 익절/손절 규칙 변화만 정량 비교. "
                      "점수가중치·기법·AI 변화는 페이퍼 실적(승률·PF)으로 검증."]
    if runner_ab:
        f, r = runner_ab
        lines += [
            "", "## 🏃 승자를 달리게 A/B (60일 · 전량익절 vs 부분익절+트레일링)",
            "| 청산방식 | 승률 | 기대값/거래 |",
            "|---|---|---|",
            f"| 전량익절 | {f.avg_win_rate}% | {f.avg_return}% |",
            f"| **부분익절+트레일링** | {r.avg_win_rate}% | **{r.avg_return}%** |",
        ]
        if f.avg_return is not None and r.avg_return is not None:
            lines.append(f"\n→ 기대값 {f.avg_return}% → **{r.avg_return}%** "
                         f"({'개선 ✅' if r.avg_return > f.avg_return else '악화 ⚠️'})")
    return "\n".join(lines) + "\n"


def changelog_line(vnum: int, note: str, changes: list[str], ab: tuple | None) -> str:
    d = date.today().isoformat()
    ab_txt = ""
    if ab and ab[0].avg_win_rate is not None and ab[1].avg_win_rate is not None:
        ab_txt = f" · 백테스트 승률 {ab[0].avg_win_rate}%→{ab[1].avg_win_rate}%"
    n = len(changes)
    return f"- **v{vnum}** ({d}) — {note} _(변경 {n}건{ab_txt})_"
