"""제안 대기열(state/pending_proposals.json) + T1/T2 분류 + 후보 파라미터 매핑 + 결정론적 ID."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

# config_key → harness._resolve_params 오버라이드 kwarg (백테 A/B 가능한 T1 레버)
T1_KEYS: dict[str, str] = {
    "risk.take1_pct": "take_pct",
    "risk.default_stop_pct": "stop_pct",
    "risk.take2_pct": "take2_pct",
    "risk.trail_pct": "trail_pct",
    "risk.max_hold_days": "max_hold",
    "risk.require_uptrend": "require_uptrend",
    "risk.min_trading_value_eok": "min_tv_eok",
}


def classify(config_key: str | None) -> str:
    return "T1" if config_key in T1_KEYS else "T2"


def candidate_params(config_key: str, suggested) -> dict:
    """T1 config_key → harness._resolve_params 오버라이드 kwarg. T2 키면 ValueError(호출 전 classify 필수)."""
    if config_key not in T1_KEYS:
        raise ValueError(f"candidate_params 는 T1 키만 — '{config_key}' 는 T2(백테 불가)")
    return {T1_KEYS[config_key]: suggested}


def direction(current, suggested) -> str:
    if isinstance(current, bool) or isinstance(suggested, bool):
        return "=true" if suggested else "=false"
    try:
        return "up" if float(suggested) > float(current) else "down"
    except (TypeError, ValueError):
        return "?"


def proposal_id(date: str, config_key: str, suggested) -> str:
    seed = f"{date}:{config_key}:{suggested}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:3].upper()


def _path(state_dir: Path) -> Path:
    return state_dir / "pending_proposals.json"


def load(state_dir: Path) -> list[dict]:
    p = _path(state_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    ps = data.get("proposals", [])
    return ps if isinstance(ps, list) else []


def save(state_dir: Path, proposals: list[dict]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _path(state_dir).write_text(
        json.dumps({"proposals": proposals}, ensure_ascii=False, indent=2), encoding="utf-8")


def find(state_dir: Path, pid: str) -> dict | None:
    for p in load(state_dir):
        if p.get("id") == pid:
            return p
    return None


def upsert(state_dir: Path, proposal: dict) -> None:
    ps = [p for p in load(state_dir) if p.get("id") != proposal["id"]]
    ps.append(proposal)
    save(state_dir, ps)


def set_status(state_dir: Path, pid: str, status: str) -> bool:
    ps = load(state_dir)
    hit = False
    for p in ps:
        if p.get("id") == pid:
            p["status"] = status
            hit = True
    if hit:
        save(state_dir, ps)
    return hit
