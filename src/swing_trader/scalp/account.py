"""단타 가상계좌 — 모델별 독립 300만. 같은 (date, market) 재정산은 덮어쓰기(멱등).

멱등이 필요한 이유: 로컬 지각 실행/클라우드 failover 로 같은 날이 두 번 정산될 수 있다
(스윙 last_run 덮어쓰기 사고의 교훈 — 여기서는 병합 대신 '같은 키 교체'가 정답:
 정산 소스가 확정 일봉이라 몇 번을 계산해도 같은 값이어야 하므로).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SEED_PER_MODEL = 3_000_000
_FILE = "scalp_state.json"
_DEFAULT_MODELS = ("v3",)   # 라이브 기본(채택 모델). load 시 config 값 주입.


def live_models(cfg) -> tuple:
    """라이브 페이퍼가 실제 운용하는 모델 — 채택 모델 1개. (비교는 scalp_compare 백테스트)"""
    adopted = str(cfg.get("scalp", "adopted_version", default="v3") or "v3").strip()
    return (adopted,) if adopted else _DEFAULT_MODELS


def _blank() -> dict:
    return {"cash": float(SEED_PER_MODEL), "realized": 0.0, "shadow_realized": 0.0}


@dataclass
class ScalpState:
    models: dict = field(default_factory=dict)
    daily: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    asOf: str = ""
    model_names: tuple = _DEFAULT_MODELS

    @classmethod
    def load(cls, state_dir: Path, models: tuple = _DEFAULT_MODELS) -> "ScalpState":
        p = Path(state_dir) / _FILE
        raw: dict = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        prev = raw.get("models") or {}
        models = tuple(models)
        # 라이브 모델만 유지 — 채택이 바뀌면 옛 모델 계좌는 자연 폐기(비교는 백테스트가 담당)
        m = {name: (prev.get(name) or _blank()) for name in models}
        return cls(models=m, daily=list(raw.get("daily") or []),
                   trades=list(raw.get("trades") or []), asOf=str(raw.get("asOf") or ""),
                   model_names=models)

    def apply_day(self, d: str, market: str, results: dict) -> None:
        # 같은 (date, market) 기존 기록 제거(재정산 멱등) — 현금/실현도 되돌린 뒤 재적용
        prev = next((r for r in self.daily if r["date"] == d and r["market"] == market), None)
        if prev:
            for m in self.model_names:
                self.models[m]["cash"] -= prev.get(f"{m}_pnl", 0.0)
                self.models[m]["realized"] -= prev.get(f"{m}_pnl", 0.0)
                self.models[m]["shadow_realized"] -= prev.get(f"{m}_shadow", 0.0)
            self.daily = [r for r in self.daily if not (r["date"] == d and r["market"] == market)]
            self.trades = [t for t in self.trades if not (t["date"] == d and t["market"] == market)]
        row = {"date": d, "market": market}
        for m in self.model_names:
            res = results.get(m) or {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []}
            self.models[m]["cash"] += res["pnl"]
            self.models[m]["realized"] += res["pnl"]
            self.models[m]["shadow_realized"] += res["shadow_pnl"]
            row[f"{m}_pnl"] = res["pnl"]
            row[f"{m}_shadow"] = res["shadow_pnl"]
            for t in res.get("trades", []):
                self.trades.append({"date": d, "market": market, "model": m, **t})
        self.daily.append(row)
        self.daily.sort(key=lambda r: (r["date"], r["market"]))
        self.asOf = d

    def save(self, state_dir: Path) -> None:
        p = Path(state_dir) / _FILE
        p.write_text(json.dumps({
            "asOf": self.asOf, "seed_per_model": SEED_PER_MODEL,
            "models": self.models, "daily": self.daily, "trades": self.trades[-400:],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
