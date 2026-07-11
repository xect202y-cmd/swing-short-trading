# 자가개선 튜닝 루프 (슬라이스 1) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI가 스윙 로직 config 수정안을 제안하고, 하니스 walk-forward A/B로 검증해 개선일 때만 Discord로 제안하며, 사람이 `swing adopt`로 승인해야 버전이 적용되는 닫힌 루프를 만든다.

**Architecture:** 기존 자산(제안 `logic_reviewer`, 심판대 `harness.compare`, 버전 `logic_version`, 학습 `LearningLog`, 알림 `notify`)을 **오케스트레이터 `review/evolve.py`**가 엮는다. 신규는 오케스트레이터 + 제안 대기열(`review/proposals.py`) + config 단일키 쓰기(`strategy/config_writer.py`) + 하니스 오버라이드 확장뿐. 채택은 100% 사람 승인.

**Tech Stack:** Python 3.11, pytest, pyyaml(기존), requests(기존). **신규 의존성 없음.**

## Global Constraints

- Python 3.11 · ruff line-length 110 (`pyproject.toml` 기준).
- **신규 의존성 금지** — config.yaml 쓰기는 pyyaml 라운드트립(주석 파괴) 대신 **타깃 라인 편집**.
- **페이퍼/백테만** 대상 — 실전 주문 로직 무관.
- **채택은 100% 사람 승인** — `evolve`는 절대 자동 적용하지 않음.
- **T2(백테 불가)는 "검증됨" 표기 금지** — "관찰 필요"로만.
- 제안 ID는 **결정론적**(날짜+키+제안값 해시) — 난수/현재시각 사용 금지.
- 한글 주석/문자열 유지(기존 코드 스타일).

## File Structure

- Create `src/swing_trader/strategy/config_writer.py` — config.yaml 단일 점표기 키 쓰기(주석 보존).
- Create `src/swing_trader/review/proposals.py` — 제안 대기열 저장 + T1/T2 분류 + 후보 파라미터 매핑 + 결정론적 ID.
- Create `src/swing_trader/review/evolve.py` — 오케스트레이터(`evaluate`/`adopt`/`reject`) + Discord 발송.
- Modify `src/swing_trader/strategy/backtest.py` — `_resolve_params`에 `max_hold/require_uptrend/min_tv_eok` 오버라이드 추가.
- Modify `src/swing_trader/main.py` — `run_evolve/run_adopt/run_reject`.
- Modify `src/swing_trader/cli.py` — `evolve/adopt/reject` 서브명령.
- Create tests: `test_resolve_params_overrides.py`, `test_config_writer.py`, `test_proposals.py`, `test_evolve.py`.

---

### Task 1: 하니스 오버라이드 확장 (`_resolve_params`)

`harness.compare(baseline, candidate)`가 `require_uptrend/max_hold/min_tv_eok`를 A/B 하려면 `_resolve_params`가 이 3개를 오버라이드로 받아야 한다. 현재는 cfg에서만 읽는다. (backward-compatible: 전부 `None` 기본값.)

**Files:**
- Modify: `src/swing_trader/strategy/backtest.py:299-319` (`_resolve_params`)
- Test: `tests/test_resolve_params_overrides.py`

**Interfaces:**
- Produces: `_resolve_params(cfg, *, take_pct=None, stop_pct=None, runner=None, take2_pct=None, trail_pct=None, max_hold=None, require_uptrend=None, min_tv_eok=None) -> dict` — 반환 dict 키 `take/stop/take2/trail/max_hold/cost/min_tv_eok/runner/require_uptrend` (기존과 동일).

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_resolve_params_overrides.py`:
```python
from swing_trader.strategy import backtest as BT


class _Cfg:
    def get(self, *keys, default=None):
        return default


def test_resolve_params_accepts_new_overrides():
    p = BT._resolve_params(_Cfg(), max_hold=10, require_uptrend=True, min_tv_eok=50)
    assert p["max_hold"] == 10
    assert p["require_uptrend"] is True
    assert p["min_tv_eok"] == 50.0


def test_resolve_params_defaults_from_cfg_when_none():
    # 오버라이드 미지정이면 cfg 기본(_Cfg.get 이 default 반환)
    p = BT._resolve_params(_Cfg())
    assert p["max_hold"] == 20            # cfg.get default
    assert p["require_uptrend"] is False  # cfg.get default
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_resolve_params_overrides.py -v`
Expected: FAIL — `test_resolve_params_accepts_new_overrides`가 `TypeError: unexpected keyword argument 'max_hold'`.

- [ ] **Step 3: 최소 구현**

`src/swing_trader/strategy/backtest.py`의 `_resolve_params` 시그니처와 3개 라인만 수정:
```python
def _resolve_params(cfg, *, take_pct=None, stop_pct=None, runner: bool | None = None,
                    take2_pct=None, trail_pct=None,
                    max_hold=None, require_uptrend=None, min_tv_eok=None) -> dict:
```
그리고 함수 본문의 해당 3줄을 오버라이드 우선으로 교체:
```python
    max_hold = int(max_hold if max_hold is not None else cfg.get("risk", "max_hold_days", default=20))
    min_tv_eok = float(min_tv_eok if min_tv_eok is not None else cfg.get("risk", "min_trading_value_eok", default=30))
    require_uptrend = bool(require_uptrend if require_uptrend is not None
                           else cfg.get("risk", "require_uptrend", default=False))
```
(기존 `fee/slip/take/stop/take2/trail/runner` 라인은 그대로 둔다. `return` dict 도 그대로.)

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_resolve_params_overrides.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 회귀 확인 + 커밋**

Run: `python -m pytest -q`
Expected: 기존 테스트 전부 통과.
```bash
git add src/swing_trader/strategy/backtest.py tests/test_resolve_params_overrides.py
git commit -m "feat(harness): _resolve_params 에 max_hold/require_uptrend/min_tv 오버라이드 추가"
```

---

### Task 2: config.yaml 단일 키 쓰기 (`config_writer.py`)

`adopt` 시 config.yaml의 한 키만 안전하게 교체. pyyaml 라운드트립은 주석을 파괴하므로 **타깃 라인 편집**. T1 키는 전부 `risk.<leaf>`이고 leaf 이름이 config 전체에서 유일 → leaf 매칭 + 기대값 가드 + 정확히 1줄.

**Files:**
- Create: `src/swing_trader/strategy/config_writer.py`
- Test: `tests/test_config_writer.py`

**Interfaces:**
- Produces: `set_config_value(config_path: Path, dotted_key: str, new_value, expected_current=None) -> None` — 값 교체(주석/포맷 보존). 매칭 0줄·복수줄·기대값 불일치면 `ValueError`.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_config_writer.py`:
```python
import pytest
from swing_trader.strategy.config_writer import set_config_value


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_replaces_value_and_preserves_comment(tmp_path):
    p = _write(tmp_path, "risk:\n  take1_pct: 6.0             # 1차 익절\n  require_uptrend: true\n")
    set_config_value(p, "risk.take1_pct", 6.5, expected_current=6.0)
    out = p.read_text(encoding="utf-8")
    assert "take1_pct: 6.5" in out
    assert "# 1차 익절" in out           # 주석 보존
    assert "require_uptrend: true" in out  # 다른 줄 불변


def test_replaces_bool(tmp_path):
    p = _write(tmp_path, "risk:\n  require_uptrend: true   # 추세필터\n")
    set_config_value(p, "risk.require_uptrend", False, expected_current=True)
    assert "require_uptrend: false" in p.read_text(encoding="utf-8")


def test_raises_on_current_mismatch(tmp_path):
    p = _write(tmp_path, "risk:\n  take1_pct: 6.0\n")
    with pytest.raises(ValueError):
        set_config_value(p, "risk.take1_pct", 6.5, expected_current=5.0)


def test_raises_when_key_absent(tmp_path):
    p = _write(tmp_path, "risk:\n  take1_pct: 6.0\n")
    with pytest.raises(ValueError):
        set_config_value(p, "risk.nonexistent", 1)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_config_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: config_writer`.

- [ ] **Step 3: 최소 구현**

`src/swing_trader/strategy/config_writer.py`:
```python
"""config.yaml 단일 키 쓰기 — pyyaml 라운드트립은 주석을 파괴하므로 타깃 라인 편집.

대상은 leaf 이름이 config 전체에서 유일한 T1 키(risk.*)뿐. 정확히 1줄만 매칭돼야 한다.
"""
from __future__ import annotations

import re
from pathlib import Path


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def set_config_value(config_path: Path, dotted_key: str, new_value, expected_current=None) -> None:
    leaf = dotted_key.split(".")[-1]
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pat = re.compile(rf"^(?P<indent>\s*){re.escape(leaf)}:\s*(?P<val>[^#\n\r]*?)\s*(?P<comment>#.*?)?(?P<nl>\r?\n?)$")
    hits = [(i, m) for i, line in enumerate(lines) if (m := pat.match(line))]
    if len(hits) != 1:
        raise ValueError(f"config 키 '{dotted_key}'(leaf={leaf}) 매칭 {len(hits)}줄 — 정확히 1줄이어야 함")
    i, m = hits[0]
    cur = m.group("val").strip()
    if expected_current is not None and cur != _fmt(expected_current):
        raise ValueError(f"'{dotted_key}' 현재값 불일치: 파일 {cur!r} ≠ 기대 {_fmt(expected_current)!r}")
    comment = m.group("comment") or ""
    tail = f"  {comment}" if comment else ""
    lines[i] = f"{m.group('indent')}{leaf}: {_fmt(new_value)}{tail}{m.group('nl')}"
    config_path.write_text("".join(lines), encoding="utf-8")
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_config_writer.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/strategy/config_writer.py tests/test_config_writer.py
git commit -m "feat(config): config.yaml 단일 키 쓰기(주석 보존 라인 편집)"
```

---

### Task 3: 제안 대기열 + 분류 + 매핑 (`proposals.py`)

제안 저장소(`state/pending_proposals.json`)와 T1/T2 분류·후보 파라미터 매핑·결정론적 ID. 전부 순수 함수 + 파일 I/O(외부 의존 없음).

**Files:**
- Create: `src/swing_trader/review/proposals.py`
- Test: `tests/test_proposals.py`

**Interfaces:**
- Produces:
  - `T1_KEYS: dict[str, str]` — config_key → `_resolve_params` 오버라이드 kwarg.
  - `classify(config_key) -> "T1"|"T2"`
  - `candidate_params(config_key, suggested) -> dict`
  - `direction(current, suggested) -> str` (`"up"|"down"|"=true"|"=false"|"?"`)
  - `proposal_id(date, config_key, suggested) -> str` (3자 대문자 해시)
  - `load(state_dir) -> list[dict]`, `save(state_dir, list)`, `find(state_dir, pid) -> dict|None`, `upsert(state_dir, dict)`, `set_status(state_dir, pid, status) -> bool`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_proposals.py`:
```python
from swing_trader.review import proposals as P


def test_classify_t1_t2():
    assert P.classify("risk.take1_pct") == "T1"
    assert P.classify("risk.min_reward_risk") == "T2"
    assert P.classify(None) == "T2"


def test_candidate_params_maps_to_override_kwarg():
    assert P.candidate_params("risk.take1_pct", 6.5) == {"take_pct": 6.5}
    assert P.candidate_params("risk.require_uptrend", False) == {"require_uptrend": False}
    assert P.candidate_params("risk.max_hold_days", 50) == {"max_hold": 50}


def test_direction():
    assert P.direction(6.0, 6.5) == "up"
    assert P.direction(-3.0, -3.5) == "down"
    assert P.direction(True, False) == "=false"


def test_proposal_id_deterministic():
    a = P.proposal_id("2026-07-11", "risk.take1_pct", 6.5)
    b = P.proposal_id("2026-07-11", "risk.take1_pct", 6.5)
    assert a == b and len(a) == 3 and a.isupper()


def test_store_roundtrip(tmp_path):
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct", "status": "pending"})
    assert P.find(tmp_path, "A3")["config_key"] == "risk.take1_pct"
    # upsert 는 같은 id 교체(중복 방지)
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct", "status": "pending"})
    assert len(P.load(tmp_path)) == 1
    assert P.set_status(tmp_path, "A3", "adopted") is True
    assert P.find(tmp_path, "A3")["status"] == "adopted"
    assert P.set_status(tmp_path, "ZZ", "adopted") is False
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_proposals.py -v`
Expected: FAIL — `ModuleNotFoundError: proposals`.

- [ ] **Step 3: 최소 구현**

`src/swing_trader/review/proposals.py`:
```python
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
        return json.loads(p.read_text(encoding="utf-8")).get("proposals", [])
    except (OSError, json.JSONDecodeError):
        return []


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
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_proposals.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/review/proposals.py tests/test_proposals.py
git commit -m "feat(evolve): 제안 대기열 + T1/T2 분류 + 후보 매핑 + 결정론적 ID"
```

---

### Task 4: 오케스트레이터 `evaluate` (제안→심판→pending/학습)

제안 생성(`logic_reviewer.build_review`) → 각 제안 분류 → T1은 `harness.compare` A/B → improve면 pending 등록+Discord, worse/neutral이면 `LearningLog`에 기각 학습(재제안 차단), T2는 관찰 안내만.

**Files:**
- Create: `src/swing_trader/review/evolve.py`
- Test: `tests/test_evolve.py`

**Interfaces:**
- Consumes: `proposals`(Task 3), `harness.compare`(기존, `ABResult.verdict/base_oos/cand_oos/n_oos`), `logic_reviewer.build_review`(기존), `learning_log.LearningLog`(기존), `config_writer.set_config_value`(Task 2), `logic_version.snapshot/save_version`(기존), `config.load_config`(기존).
- Produces: `evaluate(cfg, provider, notes, days) -> dict` — `{"ok":bool, "proposed":[dict], "rejected":[dict], "t2":[dict], "sent":bool}` (ok=False면 `reason` 포함).

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_evolve.py`:
```python
import json
import types

from swing_trader.review import evolve as EV
from swing_trader.review import proposals as P


def _cfg(tmp_path):
    return types.SimpleNamespace(
        state_dir=tmp_path,
        creds=types.SimpleNamespace(discord_webhook_url=None))


def _ab(verdict):
    rep = lambda e, s: types.SimpleNamespace(expectancy=e, sharpe=s)
    return types.SimpleNamespace(verdict=verdict, n_oos=143,
                                 base_oos=rep(0.62, 0.11), cand_oos=rep(0.70, 0.14))


def _review(suggestions):
    return ({"ok": True, "date": "2026-07-11", "suggestions": suggestions}, "evidence")


def test_evaluate_improve_creates_pending(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(EV.LR, "build_review", lambda c: _review([
        {"title": "익절상향", "insight": "i", "config_key": "risk.take1_pct",
         "current": 6.0, "suggested": 6.5}]))
    monkeypatch.setattr(EV.HN, "compare", lambda *a, **k: _ab("improve"))
    r = EV.evaluate(cfg, None, [], 500)
    props = P.load(tmp_path)
    assert r["ok"] and len(r["proposed"]) == 1
    assert len(props) == 1 and props[0]["config_key"] == "risk.take1_pct"
    assert props[0]["status"] == "pending" and props[0]["tier"] == "T1"


def test_evaluate_worse_learns_and_skips_on_rerun(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    sug = {"title": "t", "insight": "i", "config_key": "risk.default_stop_pct",
           "current": -3.0, "suggested": -3.5}
    monkeypatch.setattr(EV.LR, "build_review", lambda c: _review([sug]))
    monkeypatch.setattr(EV.HN, "compare", lambda *a, **k: _ab("worse"))
    EV.evaluate(cfg, None, [], 500)
    rules = json.loads((tmp_path / "learned_rules.json").read_text(encoding="utf-8"))
    assert any(k.startswith("reject:risk.default_stop_pct:") for k in rules)
    assert P.load(tmp_path) == []
    # 재실행: compare 를 improve 로 바꿔도 이미 기각 학습돼 재제안·재백테 안 함
    called = {"n": 0}
    def _cmp(*a, **k):
        called["n"] += 1
        return _ab("improve")
    monkeypatch.setattr(EV.HN, "compare", _cmp)
    EV.evaluate(cfg, None, [], 500)
    assert called["n"] == 0 and P.load(tmp_path) == []


def test_evaluate_t2_not_backtested(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    sug = {"title": "t", "insight": "i", "config_key": "risk.min_reward_risk",
           "current": 1.75, "suggested": 2.0}
    monkeypatch.setattr(EV.LR, "build_review", lambda c: _review([sug]))
    called = {"n": 0}
    monkeypatch.setattr(EV.HN, "compare",
                        lambda *a, **k: (called.__setitem__("n", called["n"] + 1), _ab("improve"))[1])
    r = EV.evaluate(cfg, None, [], 500)
    assert called["n"] == 0            # T2 는 백테 안 함
    assert P.load(tmp_path) == [] and len(r["t2"]) == 1


def test_evaluate_low_sample_returns_not_ok(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(EV.LR, "build_review",
                        lambda c: ({"ok": False, "reason": "청산 3건 미만"}, "ev"))
    r = EV.evaluate(cfg, None, [], 500)
    assert r["ok"] is False and "3건" in r["reason"]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_evolve.py -v`
Expected: FAIL — `ModuleNotFoundError: evolve`.

- [ ] **Step 3: 최소 구현 (evaluate + _notify)**

`src/swing_trader/review/evolve.py`:
```python
"""자가개선 튜닝 루프 오케스트레이터 — 제안→심판(harness)→사람승인 게이트.

evaluate : 제안 생성→T1 백테 A/B→개선이면 pending 등록+Discord, 악화면 학습원장 기각기록.
adopt/reject : 사람 승인/거절(버전 적용·학습 기록).
"""
from __future__ import annotations

from ..models import now_kst
from ..state.daily_marker import today_kst
from ..notify.discord import notify
from ..strategy import harness as HN
from ..strategy import logic_version as LV
from ..strategy.config_writer import set_config_value
from ..config import load_config
from .learning_log import LearningLog
from . import proposals as P
from . import logic_reviewer as LR


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
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_evolve.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/review/evolve.py tests/test_evolve.py
git commit -m "feat(evolve): evaluate — 제안→하니스 A/B→pending/학습원장 폐루프"
```

---

### Task 5: `adopt` / `reject` (사람 승인 게이트)

`adopt`는 config.yaml에 키 적용 + 로직 버전 v↑ + 학습원장 채택기록 + 상태=adopted + Discord 확인. `reject`는 상태=rejected + 학습원장 기각기록.

**Files:**
- Modify: `src/swing_trader/review/evolve.py` (함수 추가)
- Test: `tests/test_evolve.py` (테스트 추가)

**Interfaces:**
- Consumes: `set_config_value`(Task 2), `LV.snapshot/save_version`(기존), `load_config`(기존).
- Produces:
  - `adopt(cfg, pid, config_path) -> dict` — `{"ok":bool, "version":int, "note":str}` 또는 `{"ok":False, "reason":str}`.
  - `reject(cfg, pid) -> dict` — `{"ok":bool}` 또는 `{"ok":False, "reason":str}`.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_evolve.py`에 추가:
```python
def test_adopt_applies_config_and_versions(monkeypatch, tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("risk:\n  take1_pct: 6.0   # 익절\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct",
                        "current": 6.0, "suggested": 6.5, "status": "pending"})
    # snapshot/load_config 는 무겁게 실제 cfg 필요 → 스텁
    monkeypatch.setattr(EV, "load_config", lambda p: "NEWCFG")
    monkeypatch.setattr(EV.LV, "snapshot", lambda c: {"risk.take1_pct": 6.5})
    r = EV.adopt(cfg, "A3", cfgfile)
    assert r["ok"] and r["version"] >= 1
    assert "take1_pct: 6.5" in cfgfile.read_text(encoding="utf-8")
    assert P.find(tmp_path, "A3")["status"] == "adopted"
    rules = json.loads((tmp_path / "learned_rules.json").read_text(encoding="utf-8"))
    assert any(k.startswith("accept:risk.take1_pct") for k in rules)


def test_adopt_unknown_id(tmp_path):
    r = EV.adopt(_cfg(tmp_path), "ZZ", tmp_path / "config.yaml")
    assert r["ok"] is False and "없음" in r["reason"]


def test_adopt_already_processed(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct",
                        "current": 6.0, "suggested": 6.5, "status": "adopted"})
    r = EV.adopt(cfg, "A3", tmp_path / "config.yaml")
    assert r["ok"] is False and "adopted" in r["reason"]


def test_reject_records_and_learns(tmp_path):
    cfg = _cfg(tmp_path)
    P.upsert(tmp_path, {"id": "B7", "config_key": "risk.take1_pct",
                        "current": 6.0, "suggested": 6.5, "status": "pending"})
    r = EV.reject(cfg, "B7")
    assert r["ok"] and P.find(tmp_path, "B7")["status"] == "rejected"
    rules = json.loads((tmp_path / "learned_rules.json").read_text(encoding="utf-8"))
    assert any(k.startswith("reject:risk.take1_pct") for k in rules)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_evolve.py -k "adopt or reject" -v`
Expected: FAIL — `AttributeError: module has no attribute 'adopt'`.

- [ ] **Step 3: 최소 구현 (evolve.py 하단에 추가)**

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_evolve.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/review/evolve.py tests/test_evolve.py
git commit -m "feat(evolve): adopt/reject 사람 승인 게이트(config 적용·버전·학습기록)"
```

---

### Task 6: CLI + main 배선 (`evolve`/`adopt`/`reject`)

명령 3개를 cli에 노출하고 main에서 컨텍스트(provider/notes/days)를 조립.

**Files:**
- Modify: `src/swing_trader/main.py` (함수 3개 추가, 파일 하단)
- Modify: `src/swing_trader/cli.py` (서브파서 3개 + 핸들러 3개)

**Interfaces:**
- Consumes: `evolve.evaluate/adopt/reject`(Task 4-5), `harness.backtest_provider`·`_load_notes`(기존, `main.py` 내부).
- Produces: `run_evolve(cfg) -> dict`, `run_adopt(cfg, pid, config_path) -> dict`, `run_reject(cfg, pid) -> dict`.

- [ ] **Step 1: main.py 에 run 함수 추가**

`src/swing_trader/main.py` 하단(`run_harness` 부근 스타일 그대로)에 추가:
```python
def run_evolve(cfg: Config) -> dict:
    """자가개선 — 제안→하니스 A/B→개선이면 pending 등록+Discord(사람 승인 대기)."""
    from .review import evolve as _EV
    from .strategy import harness as _HN
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    market = str(cfg.get("backtest", "universe", default="all"))
    notes = [n for n in _load_notes(cfg, reader, None, market) if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    return _EV.evaluate(cfg, provider, notes, days)


def run_adopt(cfg: Config, pid: str, config_path: str | None) -> dict:
    from .review import evolve as _EV
    from .config import PROJECT_ROOT
    cfg_path = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
    return _EV.adopt(cfg, pid, cfg_path)


def run_reject(cfg: Config, pid: str) -> dict:
    from .review import evolve as _EV
    return _EV.reject(cfg, pid)
```

- [ ] **Step 2: cli.py 서브파서 추가**

`src/swing_trader/cli.py`의 `crosses` 서브파서 등록부(65행 부근) 뒤에 추가:
```python
    sub.add_parser("evolve", help="자가개선 — AI제안→하니스 A/B→개선이면 Discord 제안(사람 승인 대기)")
    ad = sub.add_parser("adopt", help="제안 채택 — config 적용+로직 버전 기록")
    ad.add_argument("id", help="제안 ID(예: A3)")
    rj = sub.add_parser("reject", help="제안 거절 — 학습원장에 기각 기록")
    rj.add_argument("id", help="제안 ID(예: A3)")
```

- [ ] **Step 3: cli.py 핸들러 추가**

`src/swing_trader/cli.py`의 `crosses` 핸들러(135행 부근) 뒤에 추가:
```python
    if args.cmd == "evolve":
        r = M.run_evolve(cfg)
        if not r.get("ok"):
            print(f"⏸ evolve 보류: {r.get('reason')}")
        else:
            print(f"✅ evolve: 제안 {len(r['proposed'])} · 기각학습 {len(r['rejected'])} · "
                  f"관찰 {len(r['t2'])}" + (" (Discord 발송)" if r["sent"] else ""))
        return 0
    if args.cmd == "adopt":
        r = M.run_adopt(cfg, args.id, args.config)
        print(f"✅ adopt #{args.id} → 로직 v{r['version']}" if r.get("ok")
              else f"❌ {r.get('reason')}")
        return 0 if r.get("ok") else 1
    if args.cmd == "reject":
        r = M.run_reject(cfg, args.id)
        print(f"✅ reject #{args.id} 기록" if r.get("ok") else f"❌ {r.get('reason')}")
        return 0 if r.get("ok") else 1
```

- [ ] **Step 4: 스모크 테스트 (인자 파싱 + 미존재 채택)**

Run: `python -m swing_trader.cli adopt ZZ`
Expected: `❌ 제안 #ZZ 없음` 출력 + 종료코드 1. (state에 pending 없으므로.)

Run: `python -m pytest -q`
Expected: 전체 통과.

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/main.py src/swing_trader/cli.py
git commit -m "feat(cli): evolve/adopt/reject 명령 — 자가개선 루프 진입점"
```

---

### Task 7: 실데이터 엔드투엔드 스모크 (선택·비파괴)

실제 볼트/데이터로 `evolve`를 한 번 돌려 크래시·경로·발송을 확인. **채택은 하지 않음**(읽기/제안까지만).

**Files:** 없음(실행만)

- [ ] **Step 1: OpenAI 키 확인**

Run: `python -m swing_trader.cli doctor`
Expected: `OpenAI 키` ✅. (없으면 evolve 는 제안 0 으로 조용히 종료 — 정상.)

- [ ] **Step 2: evolve 실행(제안까지만)**

Run: `python -m swing_trader.cli evolve -v`
Expected: `✅ evolve: 제안 N · 기각학습 M · 관찰 K` 출력. 크래시 없음. `state/pending_proposals.json` 생성(제안 있으면). Discord 웹훅 있으면 카드 수신.

- [ ] **Step 3: 산출물 확인**

Run: `python -c "import json,pathlib; print(pathlib.Path('state/pending_proposals.json').read_text(encoding='utf-8')[:800])"`
Expected: `proposals` 배열(각 항목에 `id/config_key/verdict:improve/status:pending`). 없으면 `[]`(개선 제안이 없었던 정상 케이스).

- [ ] **Step 4: (원한다면) 한 건 채택 후 되돌리기 확인**

Run: `python -m swing_trader.cli adopt <id>` → config.yaml 해당 키 변경 + `state/logic_versions.json` v↑ 확인.
되돌리려면 `git checkout config.yaml` (페이퍼라 안전).

> 이 태스크는 커밋 없음 — 실행 검증만. `state/` 변경은 기존 관행대로 별도 처리.

---

## Self-Review

**1. Spec coverage:**
- §3 재사용/신규 → Task 1(하니스 확장)·2(config_writer)·3(proposals)·4(evaluate)·5(adopt/reject)·6(cli). ✅
- §4 데이터 흐름(제안→분류→심판→pending/학습→adopt) → Task 4·5. ✅
- §5 T1/T2 경계 → Task 3 `T1_KEYS`/`classify`, Task 1이 require_uptrend/max_hold/min_tv를 실제 백테 가능케 함. ✅
- §6 자료구조(pending_proposals·learned_rules 스키마) → Task 3·4. ✅
- §8 에러 처리(키없음 조용종료·insufficient 보류·adopt 없음/중복·원자적 쓰기) → Task 4·5 테스트로 커버. ✅
- §9 성공기준 5개 → Task 4(1·2·4)·5(3)·2(5)에 대응 테스트. ✅
- §10 안전장치(사람승인·미통과 미발송·T2 미채택) → Task 4·5. ✅

**2. Placeholder scan:** "TBD/적절히 처리/테스트는 위와 같이" 없음 — 모든 스텝에 실제 코드/명령/기대출력. ✅

**3. Type consistency:** `evaluate/adopt/reject` 반환 dict 키(`ok/proposed/rejected/t2/sent/version/note/reason`)가 cli 핸들러 사용과 일치. `ABResult` 속성(`verdict/base_oos/cand_oos/n_oos`, `*.expectancy/.sharpe`)이 실제 `harness.py`와 일치. `T1_KEYS` 값이 `_resolve_params`(Task 1 확장 후) kwarg와 일치. `LearningLog.learn(rule_id, note, case)` 시그니처 일치. ✅
