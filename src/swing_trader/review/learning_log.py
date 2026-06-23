"""학습 로그 — 실패에서 도출한 차단 룰을 누적(state/learned_rules.json)."""
from __future__ import annotations

import json
from pathlib import Path


class LearningLog:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "learned_rules.json"
        self.rules: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.rules = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.rules = {}

    def learn(self, rule_id: str, note: str, case: str) -> None:
        r = self.rules.setdefault(rule_id, {"note": note, "hits": 0, "cases": []})
        r["hits"] += 1
        r["note"] = note
        if case and case not in r["cases"]:
            r["cases"].append(case)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rules, ensure_ascii=False, indent=2), encoding="utf-8")

    def as_lines(self) -> list[str]:
        out = []
        for rid, r in sorted(self.rules.items(), key=lambda kv: -kv[1]["hits"]):
            out.append(f"- [{rid}] {r['note']} (적중 {r['hits']}회)"
                       + (f" · 사례: {', '.join(r['cases'][-5:])}" if r["cases"] else ""))
        return out or ["- (학습된 룰 없음)"]
