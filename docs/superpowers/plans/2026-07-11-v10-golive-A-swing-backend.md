# v10 라이브 채택 (A: swing 백엔드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v10(신고가 거감짜름)을 KR 스윙 라이브 채택 모델로 배선 — 기존 페이퍼 브로커 계정을 인수해 청산은 v7 규칙 그대로 두고 신규 진입만 v10로 교체, 옵시디언·디스코드·앱(version_compare.json) 3면 반영.

**Architecture:** Option B(브로커 재사용). `run_v10_live`는 `main.run_once`의 KR 사이클을 본떠, **진입 신호 소스만** `SignalEngine.scan(notes)` → v10 전시장 `scan_candidates`+게이트로 교체하고, 청산(`PositionManager.check_and_exit`)·사이징/게이팅(`OrderManager.execute_signals`)·영속화(`broker.save`/`analytics`/`briefer`)는 그대로 재사용한다. 대시보드가 읽는 모든 state 파일이 단일 브로커에서 일관 파생 → split-brain 없음.

**Tech Stack:** Python 3.12, 기존 swing_trader 패키지(PaperBroker/PositionManager/OrderManager/analytics/briefer/VaultWriter), pandas/numpy, pytest.

## Global Constraints

- 대상 시장 **코스피+코스닥만**(v10은 KR 전용). 계정은 기존 **페이퍼(모의)** 계정 — 실계좌 아님.
- **정직성**: synthetic 데이터를 실거래 성과로 쓰지 않음. 패널 없으면 명확 에러(RuntimeError). 브로커 상태 손편집 금지(재조정도 코드/스크립트로, 브로커가 authoritative).
- 진입 체결가 = 거감짜름일 D 종가. 청산 = `rules.decide_exit(mode="v7")`(5일선/대량음봉/−3%/max_hold) **그대로 재사용, 신규 청산 코드 0줄**.
- 계정 파일은 **단일 브로커에서 파생**: `paper_state.json`(broker.save) → `open_positions.json`(briefer._positions_data) / `closed_trades.json`(analytics.record_closed_trades) / `equity_history.json`(analytics.record_equity).
- 수급 게이트: 라이브는 **페일오픈**(SupplyProvider None→진입 허용). 시황 게이트 데이터 없음→페일오픈.
- 멱등: 같은 확정봉 날짜 d 재실행은 무변화(broker.advance_bar + asOf 게이트).
- Python 실행 `./.venv/Scripts/python.exe`(NOT python3, Windows). 커밋 자주.
- CLI 라이브 명령은 `swing-v10`(백테스트 `swing-v10-backtest`와 구분).

---

## File Structure

- **Create** `src/swing_trader/strategy/v10_live.py` — `build_v10_signals(...)`(순수: 오늘 v10 후보→Signal 리스트) + `run_v10_live(cfg)`(라이브 사이클).
- **Modify** `src/swing_trader/obsidian/writer.py` — `append_swing_v10(content, d=None)` 추가(append_swing_us 미러).
- **Modify** `config.yaml` — `regime.adopted_version: v9→v10`, `v10.alloc_pct`/`v10.rank` 추가.
- **Modify** `src/swing_trader/main.py` — `run_version_compare`에 v10 엔트리 병합(v10_compare.json 인용).
- **Modify** `src/swing_trader/cli.py` — `swing-v10` 서브커맨드 등록·디스패치.
- **Create** `run_swing_v10.bat` + **Modify** `_register_swing_task.ps1` — 예약작업(패널 갱신+실행+state sync), v9 KR 태스크 은퇴.
- **Create** `scripts/reconcile_paper_account.py` (또는 일회성) — A0 계정 재조정.
- **Test** `tests/test_v10_live.py`.

---

## Task 1: 페이퍼 계정 split-brain 재조정 (A0 · 선행)

**Files:**
- Modify: `state/paper_state.json` (모의 계정 상태 — AMD 제거·현금 재조정)
- Test: `tests/test_v10_live.py`

**Interfaces:**
- Produces: 재조정 후 `PaperBroker(state_path=state/paper_state.json).get_positions()==[]` 및 `get_cash_balance()==open_positions.json 의 cash`.

- [ ] **Step 1: 현재 어긋남 확인**

Run: `./.venv/Scripts/python.exe -c "import json; b=json.load(open('state/paper_state.json',encoding='utf-8')); o=json.load(open('state/open_positions.json',encoding='utf-8')); print('broker cash', b['cash'], 'pos', list(b['positions'])); print('dash cash', o['cash'], 'pos', len(o['positions']))"`
Expected: broker에 AMD 있음/현금 3,829,405 · dashboard flat/현금 3,000,440 (어긋남 확인).

- [ ] **Step 2: Write the failing test**

`tests/test_v10_live.py` (신규):
```python
"""v10 라이브 — 계정 재조정 + 신호 빌더 + 라이브 루프."""
import json
from pathlib import Path

from swing_trader.broker.paper import PaperBroker


def test_paper_account_reconciled_flat():
    # A0: 재조정 후 브로커는 flat(보유 0), 현금은 대시보드 스냅샷과 일치.
    root = Path(__file__).resolve().parents[1]
    broker = PaperBroker(seed_cash=5_000_000, state_path=root / "state" / "paper_state.json")
    o = json.loads((root / "state" / "open_positions.json").read_text(encoding="utf-8"))
    assert broker.get_positions() == []
    assert broker.get_cash_balance() == round(float(o["cash"]), 2)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py::test_paper_account_reconciled_flat -v`
Expected: FAIL (broker에 AMD 보유·현금 불일치).

- [ ] **Step 4: 재조정 실행**

`state/open_positions.json`의 `cash` 값을 읽어 `state/paper_state.json`을 flat으로 재기록:
```bash
./.venv/Scripts/python.exe -c "
import json
o = json.load(open('state/open_positions.json', encoding='utf-8'))
b = json.load(open('state/paper_state.json', encoding='utf-8'))
b['cash'] = round(float(o['cash']), 2)   # 대시보드 기준 현금
b['positions'] = {}                       # AMD(유령 US잔재) 청산 확정
json.dump(b, open('state/paper_state.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)
print('reconciled: cash', b['cash'], 'positions', b['positions'])
"
```
(`realized`/`last_bar_date`는 보존 — 이력 유지.)

- [ ] **Step 5: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py::test_paper_account_reconciled_flat -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add state/paper_state.json tests/test_v10_live.py
git commit -m "fix(state): 페이퍼 계정 split-brain 재조정 — AMD 유령 청산·현금 대시보드 일치(v10 인수 선행)"
```
(참고: state/가 .gitignore면 `git add -f state/paper_state.json` 필요. 확인.)

---

## Task 2: VaultWriter.append_swing_v10 + config

**Files:**
- Modify: `src/swing_trader/obsidian/writer.py` (append_swing_us 아래에 추가)
- Modify: `config.yaml`
- Test: `tests/test_v10_live.py`

**Interfaces:**
- Consumes: `VaultWriter._path`(기존)
- Produces: `VaultWriter.append_swing_v10(content: str, d=None) -> Path` — signals_dir에 `{date}_SwingV10.md`.
- config 키 `v10.alloc_pct`(float), `v10.rank`(str), `regime.adopted_version=="v10"`.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_live.py` 에 추가:
```python
from swing_trader.config import load_config


def test_v10_config_live_knobs():
    cfg = load_config()
    assert cfg.get("regime", "adopted_version") == "v10"
    assert isinstance(cfg.get("v10", "alloc_pct"), (int, float))
    assert cfg.get("v10", "rank") in ("momentum", "newhigh_strength")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py::test_v10_config_live_knobs -v`
Expected: FAIL (adopted_version==v9, v10.alloc_pct 없음).

- [ ] **Step 3: config 수정**

`config.yaml` `regime.adopted_version: v9` → `v10`. `v10:` 블록에 추가:
```yaml
  alloc_pct: 0.20        # 신규 진입 1종목 사이징(시드 대비) — capital.first_entry 대체 시 사용
  rank: momentum         # 신규 진입 랭킹 키(momentum=종가/60일선-1)
```

- [ ] **Step 4: append_swing_v10 추가**

`src/swing_trader/obsidian/writer.py`, `append_swing_us` 정의 바로 아래:
```python
    def append_swing_v10(self, content: str, d=None):
        """스윙 V10(신고가 거감짜름) 일일 로그 → signals_dir/{date}_SwingV10.md (append_swing_us 미러)."""
        return self._append(self._path("signals_dir", "SwingV10", d), content,
                            fm={"type": "스윙V10", "tags": ["스윙", "v10", "거감짜름", "페이퍼"]})
```
*(구현 전 `append_swing_us`의 실제 시그니처/`_append`/`_path` 사용법을 writer.py에서 확인해 정확히 미러링할 것 — helper 이름이 다르면 그에 맞춤.)*

- [ ] **Step 5: append 테스트 + config 테스트 통과**

`tests/test_v10_live.py` 에 추가:
```python
def test_append_swing_v10_writes_to_signals(tmp_path, monkeypatch):
    from swing_trader.config import load_config
    from swing_trader.obsidian.writer import VaultWriter
    cfg = load_config()
    monkeypatch.setattr(cfg, "vault_root", tmp_path)   # 실제 볼트 오염 방지
    w = VaultWriter(cfg)
    p = w.append_swing_v10("### 테스트 v10\n> 내용\n", d="2026-07-11")
    assert p.exists() and "SwingV10" in p.name and "테스트 v10" in p.read_text(encoding="utf-8")
```
Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py -k "config_live or append_swing_v10" -v`
Expected: PASS (2). *(monkeypatch 방식이 VaultWriter 경로해석과 안 맞으면 cfg.write_dir/vault_root 실제 사용처를 보고 tmp 경로 주입법을 맞출 것.)*

- [ ] **Step 6: Commit**

```bash
git add config.yaml src/swing_trader/obsidian/writer.py tests/test_v10_live.py
git commit -m "feat(v10): adopted_version=v10 + v10 라이브 노브 + VaultWriter.append_swing_v10"
```

---

## Task 3: v10 신호 빌더 (순수)

**Files:**
- Create: `src/swing_trader/strategy/v10_live.py`
- Test: `tests/test_v10_live.py`

**Interfaces:**
- Consumes: `v10_new_high.scan_candidates`/`_params_from_cfg`/`regime_ok`, `market.supply.supply_ok`, `models.Signal`/`SignalKind`/`Plan`.
- Produces: `build_v10_signals(cfg, panel, d, supply, kospi_up, kosdaq_up, market_of) -> list[Signal]`
  — 각 패널 종목에서 `scan_candidates` 후보 중 `entry_date==d`인 것만, 수급(라이브 페일오픈)·시황 게이트 통과분을 `Signal(kind=BUY, plan=Plan(entry, stop, target1), score, sector, ticker, name, price)`로.

- [ ] **Step 1: Write the failing test**

`tests/test_v10_live.py` 에 추가:
```python
import numpy as np
import pandas as pd
from swing_trader.strategy import v10_live
from swing_trader.models import SignalKind


def _panel_with_todays_entry():
    # 260봉 박스 + [돌파, 거감짜름(=오늘 d)] → 오늘 진입 후보 1건.
    base = list(100 + np.sin(np.linspace(0, 12, 260)) * 2)
    closes = base + [110.0, 109.5]
    opens = list(closes[:260]) + [105.0, 110.0]
    vols = [1e6] * 260 + [3e6, 5e5]
    idx = pd.date_range("2024-01-02", periods=262, freq="B")
    df = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes),
                       "low": np.minimum(opens, closes), "close": closes,
                       "volume": vols}, index=idx)
    return df, idx[-1].strftime("%Y-%m-%d")


class _Cfg:
    def get(self, *k, default=None):
        t = {("v10",): dict(high_n=252, vol_x=2.0, body_min=0.03, min_tv_eok=0, window=3,
                            vol_dry=0.7, body_max=0.03, supply_days=3),
             ("risk", "default_stop_pct"): -3.0, ("risk", "take1_pct"): 6.0,
             ("risk", "max_hold_days"): 40}
        return t.get(tuple(k), default)


def test_build_v10_signals_todays_entry_only():
    df, d = _panel_with_todays_entry()
    sigs = v10_live.build_v10_signals(_Cfg(), {"005930": df}, d, supply=None,
                                      kospi_up=None, kosdaq_up=None, market_of={"005930": "KOSPI"})
    assert len(sigs) == 1
    s = sigs[0]
    assert s.ticker == "005930" and s.kind == SignalKind.BUY
    assert s.plan is not None and abs(s.plan.entry - 109.5) < 1e-6   # 거감짜름일 종가
    assert s.plan.stop < s.plan.entry < s.plan.target1


def test_build_v10_signals_supply_hardgate_off_in_live():
    # 라이브: 수급 None(데이터 없음) → 페일오픈으로 진입 신호 유지.
    df, d = _panel_with_todays_entry()
    sigs = v10_live.build_v10_signals(_Cfg(), {"005930": df}, d, supply=None,
                                      kospi_up=None, kosdaq_up=None, market_of={"005930": "KOSPI"})
    assert len(sigs) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py -k build_v10_signals -v`
Expected: FAIL (ModuleNotFoundError: v10_live).

- [ ] **Step 3: Write the implementation**

`src/swing_trader/strategy/v10_live.py` (신규):
```python
"""v10 라이브 — 오늘 거감짜름 진입 신호 빌더 + 라이브 사이클(Option B: 브로커 재사용).

run_once 의 KR 사이클을 본떠, 진입 신호 소스만 SignalEngine(노트) → v10 전시장 스캔으로 교체.
청산/사이징/영속화는 기존 PositionManager/OrderManager/analytics/briefer 재사용.
"""
from __future__ import annotations

from ..models import Plan, Signal, SignalKind
from ..market.supply import supply_ok
from .v10_new_high import _params_from_cfg, regime_ok, scan_candidates


def build_v10_signals(cfg, panel: dict, d: str, supply, kospi_up, kosdaq_up,
                      market_of: dict) -> list[Signal]:
    """오늘(d) 거감짜름 진입 후보 → 라이브 게이트 통과분을 매수 Signal 로.

    수급: 라이브 페일오픈(None=데이터없음 → 진입 허용). 시황: regime_ok(None=페일오픈).
    """
    p = _params_from_cfg(cfg)
    stop_pct = float(cfg.get("risk", "default_stop_pct", default=-3.0)) / 100
    take_pct = float(cfg.get("risk", "take1_pct", default=6.0)) / 100
    out: list[Signal] = []
    for ticker, df in panel.items():
        if df is None or len(df) < p["high_n"] + p["window"] + 5:
            continue
        cands = scan_candidates(
            df, ticker, high_n=p["high_n"], vol_x=p["vol_x"], body_min=p["body_min"],
            min_tv_eok=p["min_tv_eok"], window=p["window"], vol_dry=p["vol_dry"], body_max=p["body_max"])
        for c in cands:
            if c.entry_date != d:
                continue
            market = market_of.get(ticker, "KOSPI")
            if not regime_ok(market, d, kospi_up, kosdaq_up):
                continue
            netbuy = supply.institution_netbuy(ticker) if supply is not None else None
            ok = supply_ok(netbuy, d, p["supply_days"])
            if ok is False:                    # 명시적 순매도 → 차단. None(데이터없음)=페일오픈 진입.
                continue
            entry = c.entry_price
            plan = Plan(entry=entry, stop=round(entry * (1 + stop_pct), 2),
                        target1=round(entry * (1 + take_pct), 2), target2=None)
            score = 80.0 + (5.0 if c.all_time else 0.0) + (3.0 if c.hist_vol else 0.0)
            out.append(Signal(ticker=ticker, name=ticker, kind=SignalKind.BUY, score=score,
                              price=entry, plan=plan, sector=None,
                              reasons=[f"v10 거감짜름 진입(d={d})",
                                       *(["역사적 신고가"] if c.all_time else []),
                                       *(["역사적 거래량"] if c.hist_vol else [])]))
    return out
```
*(구현 전 `models.Signal`/`Plan`의 실제 필드·생성자 시그니처를 models.py에서 확인해 정확히 구성할 것. Signal이 dataclass면 필수 필드만 채우고 나머지는 기본값. Plan이 build_plan 헬퍼로만 만들어지면 그 헬퍼 사용.)*

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py -k build_v10_signals -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/v10_live.py tests/test_v10_live.py
git commit -m "feat(v10): 오늘 거감짜름 진입 신호 빌더(라이브 페일오픈 수급/시황 게이트)"
```

---

## Task 4: run_v10_live 라이브 루프

**Files:**
- Modify: `src/swing_trader/strategy/v10_live.py`
- Test: `tests/test_v10_live.py`

**Interfaces:**
- Consumes: `build_v10_signals`, `PaperBroker`, `PositionManager`, `OrderManager`, `krx_universe.{list_universe,load_cache}`, `SupplyProvider`, `v10_new_high.index_up_days`, `analytics.record_closed_trades`/`record_equity`, `briefer._positions_data`, `notify.discord.notify_embeds`, `VaultWriter.append_swing_v10`, `daily_marker`.
- Produces: `run_v10_live(cfg) -> dict {"exited","entered","held","realized","asOf"}` (멱등).

- [ ] **Step 1: Write the failing test** (모킹으로 네트워크 없이 사이클 검증)

`tests/test_v10_live.py` 에 추가:
```python
def test_run_v10_live_idempotent_and_enters(tmp_path, monkeypatch):
    """멱등: 같은 d 두 번 실행 → 두 번째는 진입 0. 인수 보유 없고 오늘 후보 1 → 진입 1."""
    df, d = _panel_with_todays_entry()
    from swing_trader.strategy import v10_live as VL
    # 패널/유니버스/지수/수급/브리핑/디스코드/옵시디언/마커를 모킹
    monkeypatch.setattr(VL, "_load_panel", lambda cfg: ({"005930": df}, {"005930": "KOSPI"}, d))
    monkeypatch.setattr(VL, "_regime_updays", lambda cfg, ma: (None, None))
    monkeypatch.setattr(VL, "_supply_provider", lambda cfg: None)   # 페일오픈
    calls = {"discord": 0, "vault": 0, "closed": 0}
    monkeypatch.setattr(VL, "_notify", lambda *a, **k: calls.__setitem__("discord", calls["discord"] + 1))
    monkeypatch.setattr(VL, "_write_vault", lambda *a, **k: calls.__setitem__("vault", calls["vault"] + 1))
    # 실제 provider(가격) 는 패널 마지막 종가로 대체하는 로컬 provider 주입
    r1 = VL.run_v10_live(_live_cfg(tmp_path))
    assert r1["entered"] == 1 and r1["held"] == 1
    r2 = VL.run_v10_live(_live_cfg(tmp_path))     # 같은 d 재실행
    assert r2["entered"] == 0                     # 멱등
```
*(이 테스트는 run_v10_live 를 내부 seam 함수(`_load_panel`/`_regime_updays`/`_supply_provider`/`_notify`/`_write_vault`)로 분해해 모킹 가능하게 만든다는 설계를 강제한다. `_live_cfg(tmp_path)`는 state_dir=tmp_path·paper_state 없음(빈 계정)·필요한 v10/risk/capital/paper 키를 주는 최소 cfg 헬퍼 — 테스트 상단에 작성. provider는 패널 최신 종가를 돌려주는 간단 stub 주입.)*

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py -k run_v10_live -v`
Expected: FAIL (run_v10_live 없음).

- [ ] **Step 3: Write the implementation**

`v10_live.py` 에 추가(seam 함수 + 메인 루프). `main.run_once`(main.py:182-294)의 KR 경로를 참고해 진입만 교체:
```python
def _load_panel(cfg):
    """(panel, market_of, d) — krx_panel.pkl 로드. 없으면 RuntimeError(synthetic 금지)."""
    from ..scalp.krx_universe import list_universe, load_cache
    panel = {k: v for k, v in load_cache(cfg.state_dir).items() if v is not None}
    if not panel:
        raise RuntimeError("krx_panel.pkl 없음 — fetch_panel 필요(synthetic 성과 금지)")
    market_of = {u["code"]: u["market"] for u in list_universe()}
    d = max(df.index[-1].strftime("%Y-%m-%d") for df in panel.values())
    return panel, market_of, d


def _regime_updays(cfg, ma):
    from .v10_new_high import index_up_days
    if not bool(cfg.get("v10", "regime_gate", default=True)):
        return None, None
    return index_up_days("KS11", ma), index_up_days("KQ11", ma)


def _supply_provider(cfg):
    from ..market.supply import SupplyProvider
    return SupplyProvider(cfg.state_dir, max_pages=int(cfg.get("v10", "supply_max_pages", default=20)))


def _notify(cfg, embed, md):
    from ..notify.discord import notify_embeds
    notify_embeds(cfg.creds.discord_webhook_url, [embed], md)


def _write_vault(cfg, md, d):
    from ..obsidian.writer import VaultWriter
    VaultWriter(cfg).append_swing_v10(md, d)


def run_v10_live(cfg) -> dict:
    """v10 KR 스윙 라이브 1사이클 — 브로커 인수·v7 청산·v10 진입·3면 브리핑(멱등)."""
    from ..broker.paper import PaperBroker
    from ..execution.position_manager import PositionManager
    from ..execution.order_manager import OrderManager
    from ..market.data_provider import DataProvider
    from ..review import analytics as _A
    from ..review import briefer as _B
    from ..state import daily_marker as _DM

    panel, market_of, d = _load_panel(cfg)
    provider = DataProvider(cfg.get("market_data", "provider", default="auto"))
    seed = float(cfg.get("capital", "seed", default=5_000_000))
    broker = PaperBroker(seed_cash=seed, state_path=cfg.state_dir / "paper_state.json",
                         price_fn=lambda t: provider.get_ohlcv(t)[0]["close"].iloc[-1] if t else None,
                         fee_bps=float(cfg.get("paper", "fee_bps", default=1.5)),
                         slippage_bps=float(cfg.get("paper", "slippage_bps", default=5.0)))
    fresh = broker.advance_bar(d)                 # 하루 1회 bars_held++ (멱등 게이트)
    exited = entered = 0
    closed_recs = []
    if fresh:
        pm = PositionManager(cfg, broker, provider)
        for _order, _reasons, closed in pm.check_and_exit():   # v7 청산(인수 보유 포함)
            closed_recs.append(closed); exited += 1
        ma = int(cfg.get("v10", "regime_ma", default=50))
        kospi_up, kosdaq_up = _regime_updays(cfg, ma)
        supply = _supply_provider(cfg)
        signals = build_v10_signals(cfg, panel, d, supply, kospi_up, kosdaq_up, market_of)
        res = OrderManager(cfg, broker).execute_signals(signals)   # 사이징/게이팅 재사용
        entered = len(res.placed)
        broker.save()
        if closed_recs:
            _A.record_closed_trades(cfg.state_dir, closed_recs)
    # 영속화 — 대시보드 파일 전부 단일 브로커에서 파생
    pos, hv = _B._positions_data(cfg, broker, provider)          # open_positions.json
    _A.record_equity(cfg.state_dir, d, broker.get_cash_balance(), hv, seed)  # equity_history.json
    # 브리핑(디스코드 + 옵시디언)
    op_lines = [f"  · {p['name']}({p['ticker']}) {p['qty']}주 · {p['ret']:+.1f}% · {p['days']}일" for p in pos[:15]] or ["  · 없음"]
    fields = [{"name": f"📤 청산 {exited}건", "value": "\n".join(c["ticker"]+" "+str(c["return_pct"])+"%" for c in closed_recs)[:1024] or "없음", "inline": False},
              {"name": f"📥 신규 진입 {entered}건", "value": "\n".join(o.symbol for o in (res.placed if fresh else []))[:1024] or "없음", "inline": False},
              {"name": f"📊 보유 {len(pos)}종목", "value": "\n".join(op_lines)[:1024], "inline": False}]
    embed = {"title": f"🚀 스윙 V10 · {d}", "color": 0xE74C3C, "fields": fields,
             "footer": {"text": f"신고가 거감짜름 · KR 코스피+코스닥 · 실현 {broker.realized_pnl:,.0f}원"}}
    md = (f"### 🚀 스윙 V10 · {d}\n> 신고가 거감짜름(보컬 김영준) · 청산 v7(5일선) · 진입 거감짜름\n"
          f"**청산 {exited} · 진입 {entered} · 보유 {len(pos)}**\n" + "\n".join(op_lines) + "\n")
    _notify(cfg, embed, md)
    _write_vault(cfg, md, d)
    _DM.record_done(cfg.state_dir, "swing_v10", _DM.now_kst() if hasattr(_DM, "now_kst") else __import__("datetime").datetime.now(_DM.KST))
    return {"exited": exited, "entered": entered, "held": len(pos),
            "realized": round(broker.realized_pnl), "asOf": d}
```
*(구현 시 확인·정정할 것: `OrderManager` 생성자 실제 인자(cfg, broker, ...) — order_manager.py 확인; `res.placed`가 Order 리스트인지; `record_closed_trades`/`record_equity`/`_positions_data`의 정확한 인자; `daily_marker`의 현재시각 헬퍼명(KST). 테스트가 seam 모킹으로 이들을 우회하므로 단위테스트는 통과하되, 실제 호출부 시그니처를 소스로 대조.)*

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py -k run_v10_live -v`
Expected: PASS. 전체: `./.venv/Scripts/python.exe -m pytest -q` 그린.

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/strategy/v10_live.py tests/test_v10_live.py
git commit -m "feat(v10): run_v10_live 라이브 루프 — 브로커 인수·v7 청산·v10 진입·3면 브리핑(멱등)"
```

---

## Task 5: version_compare.json v10 엔트리 (앱 성과탭 노출)

**Files:**
- Modify: `src/swing_trader/main.py` (`run_version_compare`, main.py:669-784)
- Test: `tests/test_v10_live.py`

**Interfaces:**
- Consumes: `run_v10_backtest`가 쓴 `state/v10_compare.json`(v10 OOS 지표), `cfg.get("regime","adopted_version")`.
- Produces: version_compare.json 의 `versions[]`에 `{"label":"v10","title":...,"core_logic":[...],"oos":{...},"equity":[],"replay":null}` 병합, `adopted` 필드는 config에서 "v10".

- [ ] **Step 1: Write the failing test**

```python
def test_version_compare_includes_v10_entry(tmp_path):
    import json
    from swing_trader import main as m
    # v10_compare.json 최소본 준비
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "v10_compare.json").write_text(json.dumps({
        "v10": {"oos": {"expectancy": 3.34, "profit_factor": 3.0, "max_drawdown": -45.1,
                        "win_rate": 33.3, "n_trades": 183, "sharpe": None}},
        "verdict": {"winner": "v10"}}), encoding="utf-8")
    entry = m._v10_versions_entry(tmp_path)     # 신규 순수 헬퍼
    assert entry is not None
    assert entry["label"] == "v10"
    assert entry["oos"]["expectancy"] == 3.34 and entry["oos"]["n_trades"] == 183
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py -k version_compare_includes_v10 -v`
Expected: FAIL (AttributeError: _v10_versions_entry).

- [ ] **Step 3: 헬퍼 추가 + run_version_compare 병합**

`main.py` 에 순수 헬퍼 추가:
```python
def _v10_versions_entry(state_dir) -> dict | None:
    """v10_compare.json(백테 결과)에서 대시보드 버전비교용 v10 엔트리 생성. 없으면 None."""
    import json
    p = state_dir / "v10_compare.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    oos = (d.get("v10") or {}).get("oos") or {}
    return {
        "label": "v10", "title": "v10 · 신고가 거감짜름",
        "core_logic": ["52주 신고가 장대양봉 대량거래 돌파 → 첫 거감짜름(거래량 마름 짧은음봉) 종가 매수",
                       "기관 연속 순매수 게이트(백테 하드/라이브 페일오픈) + 코스닥/코스피 50일선 시황",
                       "청산 v7(5일선 이탈/대량음봉/−3%/max_hold)"],
        "oos": {"expectancy": oos.get("expectancy"), "profit_factor": oos.get("profit_factor"),
                "max_drawdown": oos.get("max_drawdown"), "sharpe": oos.get("sharpe"),
                "win_rate": oos.get("win_rate"), "n_trades": oos.get("n_trades"),
                "cum_return_pct": None},
        "equity": [], "replay": None,
    }
```
그리고 `run_version_compare` 의 `out` 조립 직후(`path.write_text` 전)에:
```python
    v10e = _v10_versions_entry(cfg.state_dir)
    if v10e and not any(x.get("label") == "v10" for x in out):
        out.append(v10e)
```
(`adopted`는 이미 `cfg.get("regime","adopted_version")`에서 "v10" — Task 2에서 flip됨.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_v10_live.py -k version_compare_includes_v10 -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/main.py tests/test_v10_live.py
git commit -m "feat(v10): version_compare.json에 v10 엔트리 병합 — 앱 성과탭 '채택됨' 노출"
```

---

## Task 6: CLI swing-v10 + bat + 예약작업

**Files:**
- Modify: `src/swing_trader/cli.py`
- Create: `run_swing_v10.bat`
- Modify: `_register_swing_task.ps1`

**Interfaces:**
- Consumes: `run_v10_live`
- Produces: CLI `swing-v10` 명령, 예약작업 `Swing-V10`, v9 KR 태스크 은퇴.

- [ ] **Step 1: CLI 등록·디스패치**

`cli.py`, 다른 `sub.add_parser` 근처:
```python
    sub.add_parser("swing-v10", help="스윙 v10(신고가 거감짜름) KR 라이브 1사이클 → 3면 브리핑 🚀")
```
디스패치(기존 `args.cmd == "..."` 패턴):
```python
    if args.cmd == "swing-v10":
        from swing_trader.strategy.v10_live import run_v10_live
        r = run_v10_live(cfg)
        print(f"✅ swing-v10: 청산 {r['exited']} · 진입 {r['entered']} · 보유 {r['held']} · 실현 {r['realized']:,}원 ({r['asOf']})")
        return 0
```

- [ ] **Step 2: 등록 확인(백테스트 미실행)**

Run: `./.venv/Scripts/python.exe -m swing_trader.cli swing-v10 --help`
Expected: usage 출력(에러 없음). **실제 실행은 Task 7.**

- [ ] **Step 3: run_swing_v10.bat 작성**

`run_swing_v10.bat`(기존 `run_swing_us.bat` 패턴 — 확인 후 미러). 내용: (1) 패널 갱신
`python -c "from pathlib import Path; from swing_trader.scalp.krx_universe import fetch_panel; fetch_panel(Path('state'))"`,
(2) `python -m swing_trader.cli swing-v10`, (3) git add -f state/commit/push 동기화 블록(기존 bat에서 복사).
*(정확한 venv 경로·PYTHONUTF8·git 블록은 run_swing_us.bat에서 그대로 가져올 것.)*

- [ ] **Step 4: 예약작업 등록 스크립트 수정**

`_register_swing_task.ps1`에 `Swing-V10` 항목 추가(KR EOD 슬롯, 예: 16:10 KST 평일 — 실제 KR 장마감 후·수급 확정 시각 고려). v9 KR 스윙 태스크(`Swing-KR`)는 은퇴(등록 제외 또는 Disable). 기존 항목 형식 그대로 따름.

- [ ] **Step 5: Commit**

```bash
git add src/swing_trader/cli.py run_swing_v10.bat _register_swing_task.ps1
git commit -m "feat(v10): swing-v10 CLI + run_swing_v10.bat + 예약작업(Swing-V10), v9 KR 태스크 은퇴"
```

---

## Task 7: 실데이터 1사이클 검증 (네트워크 · 사용자 확인)

**Files:** (없음 — 실행 검증)

- [ ] **Step 1: 패널 최신화 확인**

Run: `./.venv/Scripts/python.exe -c "from pathlib import Path; from swing_trader.scalp.krx_universe import load_cache; p=load_cache(Path('state')); print('panel', len([1 for v in p.values() if v is not None]))"`
Expected: 2000+ 종목. 오래됐으면 `fetch_panel` 갱신.

- [ ] **Step 2: 라이브 1사이클 실행(백그라운드·UTF-8)**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m swing_trader.cli swing-v10`
Expected: `✅ swing-v10: 청산 N · 진입 M · 보유 K …` + 디스코드 🚀 발송 + 옵시디언 SwingV10 파일 + state 갱신.
*(디스코드/옵시디언 부작용 있어 사용자 확인 후 실행 — Task 12(백테) 때와 동일.)*

- [ ] **Step 3: 3면 산출물 검증**

- `state/paper_state.json`·`open_positions.json`·`closed_trades.json`·`equity_history.json` 일관(단일 브로커 파생) 확인.
- `swing-trader versions`(version_compare 갱신) 실행 → `state/version_compare.json`에 v10 엔트리·`adopted:"v10"` 확인.
- 옵시디언 `04_Trading/Signals/{d}_SwingV10.md` 생성 확인.

- [ ] **Step 4: 멱등 재실행**

Run: 같은 명령 재실행 → `진입 0`(같은 d) 확인(멱등).

- [ ] **Step 5: Commit(state 동기화)**

```bash
git add -f state/paper_state.json state/open_positions.json state/version_compare.json
git commit -m "test(v10): 라이브 1사이클 실행 — 3면 산출물·멱등 검증"
```

---

## Self-Review 메모
- **스펙 커버리지**: A0 재조정→T1, A1 라이브루프→T3·T4, A2 config→T2, A3 version_compare→T5, A4 CLI/bat/예약→T6, A5 에러/정직→T3·T4·T7(패널없음 RuntimeError·수급 페일오픈). append_swing_v10→T2.
- **플레이스홀더**: 코드 스텝은 실제 코드 포함. "구현 전 확인" 주석은 기존 파일 시그니처 대조 지시(models.Signal/Plan·OrderManager 생성자·analytics/briefer 인자·writer helper·daily_marker KST) — 파일상태 의존이라 의도적. 구현자는 해당 소스를 읽어 정확히 맞춤.
- **타입 일관성**: `build_v10_signals`(T3)가 만든 `Signal`/`Plan`을 T4의 `OrderManager.execute_signals`가 소비(기존 계약). 거래 산출물은 briefer/analytics 기존 규약. `Candidate` 필드(entry_date/entry_price/all_time/hist_vol) T3에서 소비.
- **리스크**: T4의 run_v10_live는 다수 기존 함수 호출 조립 — 단위테스트는 seam 모킹으로 로직 검증, 실제 시그니처 정합은 T7 실행에서 최종 확인. models.Signal/Plan·OrderManager 생성자 시그니처가 예상과 다르면 T3/T4 구현자가 소스 대조해 정정(BLOCKED 시 에스컬레이션).
