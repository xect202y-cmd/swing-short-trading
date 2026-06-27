# 스윙 액티브-패시브 페일오버 + 로컬 장애 디스코드 알림 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬 노트북 Swing을 주(主), 클라우드 GitHub Actions를 페일오버로 만들어 중복 Hook·04_Trading 충돌을 없애고, 로컬 장애 시 디스코드로 알린다.

**Architecture:** swing repo의 `state/daily_done.json` 날짜 마커로 조율. 로컬 run-once 성공 시 시장별 마커 기록 후 push, 클라우드는 09:35(유예 30분)에 마커를 읽어 빠진 시장만 보충하고 보충 시 Discord 경고. 데이터 파이프라인 워치독은 기존 텔레그램에 Discord 발송을 병행.

**Tech Stack:** Python 3.12, argparse CLI(`swing-trader`), pytest, GitHub Actions(bash), Vercel Cron(`vercel.json`), Windows .bat, `requests`.

## Global Constraints

- 마커 파일: swing repo `state/daily_done.json`, 포맷 `{ "YYYY-MM-DD": { "us": "<iso8601 KST>", "kr": "<iso8601 KST>" } }`.
- 날짜 기준은 **KST**(`timezone(timedelta(hours=9))`). 시장 키는 `"us"`, `"kr"`만.
- 마커 보관 7일(초과 prune).
- Discord 발송은 swing 채널 = `SWING_DISCORD_WEBHOOK_URL`(없으면 `DISCORD_WEBHOOK_URL` 폴백). swing 코드는 `cfg.creds.discord_webhook_url`로 접근.
- .bat 수정 시 **주석은 PURE ASCII(영문)만** — 기존 한글 REM 인코딩 함정 회피.
- 클라우드 유예 = 30분 → vercel cron `5 0 * * 1-5` → `35 0 * * 1-5`.
- 페일오버 판단 불가(마커 읽기/네트워크 실패) 시 **보충 실행 쪽으로 bias**(Hook 놓침 방지).

---

### Task 1: 날짜 마커 모듈 (`daily_marker`)

순수 함수로 마커 읽기/기록/prune/조회. 단위 테스트 대상.

**Files:**
- Create: `src/swing_trader/state/__init__.py` (빈 파일)
- Create: `src/swing_trader/state/daily_marker.py`
- Test: `tests/test_daily_marker.py`

**Interfaces:**
- Produces:
  - `KST: timezone`
  - `today_kst() -> datetime.date`
  - `is_done(state_dir: Path, market: str, today: date) -> bool`
  - `record_done(state_dir: Path, market: str, now: datetime) -> None`  (market=="all"이면 us·kr 모두 기록)

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_daily_marker.py
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from swing_trader.state import daily_marker as DM


def test_is_done_false_when_no_file(tmp_path: Path):
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is False


def test_record_then_is_done(tmp_path: Path):
    now = datetime(2026, 6, 29, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "kr", now)
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is True
    assert DM.is_done(tmp_path, "us", date(2026, 6, 29)) is False


def test_record_all_records_both_markets(tmp_path: Path):
    now = datetime(2026, 6, 29, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "all", now)
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is True
    assert DM.is_done(tmp_path, "us", date(2026, 6, 29)) is True


def test_prune_drops_keys_older_than_7_days(tmp_path: Path):
    old = datetime(2026, 6, 1, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "kr", old)
    new = datetime(2026, 6, 29, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "kr", new)
    data = json.loads((tmp_path / "daily_done.json").read_text(encoding="utf-8"))
    assert "2026-06-01" not in data
    assert "2026-06-29" in data


def test_corrupt_file_is_treated_as_empty(tmp_path: Path):
    (tmp_path / "daily_done.json").write_text("{not json", encoding="utf-8")
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_daily_marker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'swing_trader.state'`

- [ ] **Step 3: 모듈 구현**

```python
# src/swing_trader/state/__init__.py
```
(빈 파일)

```python
# src/swing_trader/state/daily_marker.py
"""액티브-패시브 페일오버용 일일 실행 마커.

state_dir/daily_done.json: { "YYYY-MM-DD": { "us": "<iso ts>", "kr": "<iso ts>" } }
오늘 날짜 아래 시장 키가 있으면 그 시장 런이 성공 완료된 것. 클라우드는 이를 읽어
로컬이 이미 돌린 시장을 건너뛴다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
_MARKETS = ("us", "kr")
_KEEP_DAYS = 7
_FILE = "daily_done.json"


def today_kst() -> date:
    return datetime.now(KST).date()


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / _FILE


def _load(state_dir: Path) -> dict:
    p = _path(state_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _parse(key: str) -> date | None:
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


def _prune(data: dict, today: date, keep_days: int = _KEEP_DAYS) -> dict:
    cutoff = today - timedelta(days=keep_days)
    out = {}
    for k, v in data.items():
        d = _parse(k)
        if d is not None and d >= cutoff:
            out[k] = v
    return out


def is_done(state_dir: Path, market: str, today: date) -> bool:
    return market in _load(state_dir).get(today.isoformat(), {})


def record_done(state_dir: Path, market: str, now: datetime) -> None:
    if market == "all":
        for m in _MARKETS:
            record_done(state_dir, m, now)
        return
    today = now.astimezone(KST).date()
    data = _prune(_load(state_dir), today)
    data.setdefault(today.isoformat(), {})[market] = now.astimezone(KST).isoformat()
    _path(state_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_daily_marker.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/state/__init__.py src/swing_trader/state/daily_marker.py tests/test_daily_marker.py
git commit -m "feat(failover): 일일 실행 마커 모듈(record/is_done/prune)"
```

---

### Task 2: `run_once` 성공 시 마커 기록

**Files:**
- Modify: `src/swing_trader/main.py` (run_once 끝 `return {...}` 직전, 현재 ~220행)
- Test: `tests/test_daily_marker.py` (통합 케이스 추가)

**Interfaces:**
- Consumes: `daily_marker.record_done`, `daily_marker.KST` (Task 1)
- Produces: run_once 완료 후 `state_dir/daily_done.json`에 해당 market 기록

- [ ] **Step 1: 실패 테스트 작성** — run_once가 끝나면 마커가 남는지(가벼운 통합). run_once 전체 실행은 무거우므로, 기록 호출 지점을 검증하는 단위 테스트로 대체한다.

```python
# tests/test_daily_marker.py 에 추가
def test_record_done_writes_iso_kst_timestamp(tmp_path):
    from datetime import datetime
    now = datetime(2026, 6, 29, 6, 0, tzinfo=DM.KST)
    DM.record_done(tmp_path, "us", now)
    data = json.loads((tmp_path / "daily_done.json").read_text(encoding="utf-8"))
    assert data["2026-06-29"]["us"].startswith("2026-06-29T06:00")
    assert "+09:00" in data["2026-06-29"]["us"]
```

- [ ] **Step 2: 테스트 실패/통과 확인**

Run: `python -m pytest tests/test_daily_marker.py::test_record_done_writes_iso_kst_timestamp -v`
Expected: PASS (Task1 구현으로 이미 통과 — 회귀 가드)

- [ ] **Step 3: run_once에 기록 호출 추가**

`src/swing_trader/main.py`의 `run_once(...)` 함수에서 결과 dict를 `return` 하기 직전 줄에 추가:

```python
    # 페일오버 마커: 이 시장 런이 정상 완료됨을 기록(클라우드가 읽어 중복 방지)
    from .state import daily_marker as _DM
    _DM.record_done(cfg.state_dir, market, datetime.now(_DM.KST))
    return {
        # ... 기존 반환 dict 그대로 ...
```
(파일 상단에 `from datetime import datetime`이 이미 import 되어 있는지 확인; 없으면 추가. 현재 main.py는 `timedelta`를 쓰므로 datetime 계열 import 존재.)

- [ ] **Step 4: 스모크 — import 깨짐 없는지**

Run: `python -c "import swing_trader.main"`
Expected: 에러 없이 종료(exit 0)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/main.py tests/test_daily_marker.py
git commit -m "feat(failover): run_once 완료 시 일일 마커 기록"
```

---

### Task 3: CLI `check-done` + `notify-failover`

**Files:**
- Modify: `src/swing_trader/cli.py` (서브파서 2개 추가 + 디스패치)
- Test: `tests/test_cli_failover.py`

**Interfaces:**
- Consumes: `daily_marker.is_done`, `daily_marker.today_kst` (Task 1); `notify` (`notify/discord.py`); `cfg.creds.discord_webhook_url`
- Produces:
  - `swing-trader check-done --market <kr|us>` → exit 0(완료) / 1(미완료)
  - `swing-trader notify-failover --markets "kr us"` → swing 채널 경고 발송, exit 0

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_cli_failover.py
from datetime import datetime
from pathlib import Path

from swing_trader.cli import main as cli_main
from swing_trader.state import daily_marker as DM


def _seed_cfg(tmp_path, monkeypatch):
    # check-done은 cfg.state_dir만 필요 — load_config을 가벼운 더미로 패치
    class _Cfg:
        state_dir = tmp_path
        class creds:  # noqa
            discord_webhook_url = None
    monkeypatch.setattr("swing_trader.cli.load_config", lambda config_path=None: _Cfg)
    return _Cfg


def test_check_done_exit1_when_not_done(tmp_path, monkeypatch):
    _seed_cfg(tmp_path, monkeypatch)
    assert cli_main(["check-done", "--market", "kr"]) == 1


def test_check_done_exit0_when_done(tmp_path, monkeypatch):
    _seed_cfg(tmp_path, monkeypatch)
    DM.record_done(tmp_path, "kr", datetime.now(DM.KST))
    assert cli_main(["check-done", "--market", "kr"]) == 0
```
(주의: 테스트는 `cli.py`가 `load_config`를 모듈 전역에서 import해 호출한다고 가정. 현재 cli.py 디스패치가 `from .config import load_config` 패턴이면 monkeypatch 경로를 실제 import 위치로 맞춘다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_cli_failover.py -v`
Expected: FAIL — `check-done` 서브커맨드 없음(SystemExit 2) 또는 분기 미존재

- [ ] **Step 3: cli.py에 서브파서 + 디스패치 추가**

`main(argv)`의 서브파서 정의부(다른 `sub.add_parser` 옆)에 추가:
```python
    cd = sub.add_parser("check-done", help="오늘 해당 시장 런 완료 마커 있으면 exit 0, 없으면 1")
    cd.add_argument("--market", choices=["kr", "us"], required=True)
    nf = sub.add_parser("notify-failover", help="로컬 미실행 → 클라우드 대체 경고를 swing 채널로 발송")
    nf.add_argument("--markets", required=True, help='공백 구분, 예: "kr us"')
```

디스패치부(`if args.cmd == "run-once":` 들과 같은 레벨)에 추가:
```python
    if args.cmd == "check-done":
        from .state import daily_marker as DM
        return 0 if DM.is_done(cfg.state_dir, args.market, DM.today_kst()) else 1
    if args.cmd == "notify-failover":
        from .notify.discord import notify
        from .state import daily_marker as DM
        ts = DM.today_kst().isoformat()
        mk = " ".join(m.upper() for m in args.markets.split())
        msg = (f"⚠️ 로컬 스윙 미실행 감지 — 클라우드가 [{mk}] 대체 처리함. "
               f"노트북(로컬 Swing 작업) 점검 요망. ({ts} KST)")
        notify(cfg.creds.discord_webhook_url, msg)
        return 0
```
(`cfg`는 기존 디스패치에서 `cfg = load_config(args.config)`로 이미 로드됨 — 동일 변수 사용.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_cli_failover.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/swing_trader/cli.py tests/test_cli_failover.py
git commit -m "feat(failover): check-done/notify-failover CLI"
```

---

### Task 4: 로컬 .bat 마커 push

로컬 run 후 마커를 swing repo로 push해야 클라우드가 본다. **prereq: 로컬에서 `origin`(swing repo)으로 push 가능한 자격증명 구성**(PAT in remote URL 또는 Windows 자격증명 관리자).

**Files:**
- Modify: `run_swing_kr.bat` (heartbeat echo 직전)
- Modify: `run_swing_us.bat` (heartbeat echo 직전)

**Interfaces:**
- Consumes: Task 2가 만든 `state/daily_done.json`

- [ ] **Step 1: 두 .bat에 마커 push 블록 추가 (ASCII 주석만)**

`run_swing_kr.bat`과 `run_swing_us.bat` 각각, `echo %date% %time% SWING ... DONE> ...heartbeat.txt` 줄 **직전**에 삽입:
```bat
REM push daily-done marker to swing repo so cloud failover can detect local ran
git add state\daily_done.json
git diff --cached --quiet || ( git commit -m "chore(state): local daily marker [skip ci]" && git push origin HEAD )
```

- [ ] **Step 2: 수동 검증 — 마커 생성 후 push 동작**

로컬에서 한 시장 1회 실행 후 확인:
```bash
"./.venv/Scripts/swing-trader.exe" run-once --market us --no-brief
git log --oneline -1            # "chore(state): local daily marker" 보여야
git ls-files state/daily_done.json   # 추적됨 확인
```
Expected: 마커 커밋 생성 + (네트워크 정상 시) origin push 성공. push 실패해도 다음 회차 재시도(클라우드는 그날 보충 — 안전측).

- [ ] **Step 3: 커밋**

```bash
git add run_swing_kr.bat run_swing_us.bat
git commit -m "feat(failover): 로컬 런 후 daily 마커 swing repo push"
```

---

### Task 5: 클라우드 `swing.yml` 페일오버 가드 + 경고

**Files:**
- Modify: `.github/workflows/swing.yml` (기존 "실행(시장별 best-effort)" 스텝 교체)

**Interfaces:**
- Consumes: `swing-trader check-done`, `swing-trader run-once`, `swing-trader review`, `swing-trader brief`, `swing-trader notify-failover` (Task 3)

- [ ] **Step 1: 실행 스텝을 마커 가드 버전으로 교체**

`swing.yml`의 `- name: 실행(시장별 best-effort)` 스텝 `run:` 블록을 아래로 교체:
```yaml
      - name: 마커 확인 → 빠진 시장만 보충(페일오버)
        run: |
          set +e
          ALERT=""
          for M in us kr; do
            if swing-trader check-done --market "$M"; then
              echo "::notice::$M 로컬 완료 마커 존재 — skip"
            else
              echo "::group::run-once $M (클라우드 보충)"
              if [ "$M" = "us" ]; then
                swing-trader run-once --market us --no-brief; echo "exit=$?"
              else
                swing-trader run-once --market kr; echo "exit=$?"
              fi
              echo "::endgroup::"
              ALERT="$ALERT $M"
            fi
          done
          if [ -n "$ALERT" ]; then
            echo "::group::review + 주간/월간 brief + 페일오버 경고"
            swing-trader review; echo "review exit=$?"
            swing-trader brief --period auto; echo "brief exit=$?"
            swing-trader notify-failover --markets "$ALERT"; echo "notify exit=$?"
            echo "::endgroup::"
          else
            echo "::notice::양 시장 로컬 완료 — 클라우드 no-op(커밋/발송 없음)"
          fi
```
(이후 "상태 커밋백"·"리포트 커밋백" 스텝은 그대로 — 변경 없으면 자연히 no-op.)

- [ ] **Step 2: 로컬에서 가드 로직 스모크(yaml은 CI에서만 완전검증)**

bash로 분기만 모사 검증:
```bash
cd /c/Users/xect2/swing-short-trading
# 마커에 둘 다 채우면 skip, 비우면 실행 분기 — check-done 종료코드만 확인
"./.venv/Scripts/swing-trader.exe" check-done --market kr; echo "kr exit=$?"
```
Expected: 마커 유무에 따라 exit 0/1 정확. (full 워크플로는 CI 1회 수동 dispatch로 확인.)

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/swing.yml
git commit -m "feat(failover): swing.yml 마커 가드 + 페일오버 경고"
```

---

### Task 6: 데이터 파이프라인 워치독 Discord 발송

**Files:**
- Modify: `C:/Users/xect2/obsidian-automation/pipeline_watchdog.py`
- Test: `C:/Users/xect2/obsidian-automation/tests/test_watchdog_discord.py` (없으면 tests 폴더 생성)

이 파일은 **별도 레포(obsidian-automation)** 다. 해당 레포에서 커밋.

**Interfaces:**
- Consumes: `gpt-api/.env`의 `SWING_DISCORD_WEBHOOK_URL`(없으면 `DISCORD_WEBHOOK_URL`)
- Produces: stale/복구 시 텔레그램 + Discord 둘 다 발송

- [ ] **Step 1: 실패 테스트 작성** — `send_discord`가 webhook 없으면 False, 있으면 POST 시도(requests 모킹).

```python
# obsidian-automation/tests/test_watchdog_discord.py
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pwd", HERE / "pipeline_watchdog.py")
pwd = importlib.util.module_from_spec(spec); spec.loader.exec_module(pwd)


def test_send_discord_no_webhook_returns_false(monkeypatch):
    monkeypatch.setattr(pwd, "read_env", lambda p: {})
    assert pwd.send_discord("hi") is False


def test_send_discord_posts_when_webhook(monkeypatch):
    monkeypatch.setattr(pwd, "read_env", lambda p: {"SWING_DISCORD_WEBHOOK_URL": "https://x/y"})
    calls = {}
    class _R:  # fake response
        ok = True
    def _post(url, **kw):
        calls["url"] = url; calls["json"] = kw.get("json"); return _R()
    import requests
    monkeypatch.setattr(requests, "post", _post)
    assert pwd.send_discord("hi") is True
    assert calls["url"] == "https://x/y"
    assert calls["json"]["content"] == "hi"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest obsidian-automation/tests/test_watchdog_discord.py -v` (또는 해당 폴더에서)
Expected: FAIL — `AttributeError: module 'pwd' has no attribute 'send_discord'`

- [ ] **Step 3: `send_discord` 추가 + stale/복구에서 병행 호출**

`pipeline_watchdog.py`의 `send_telegram` 아래에 추가:
```python
def send_discord(text):
    import requests
    env = read_env(GPT_ENV)
    url = env.get("SWING_DISCORD_WEBHOOK_URL") or env.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("  Discord 웹훅 없음 — 발송 생략")
        return False
    try:
        # 텔레그램용 <b> 태그 제거(디스코드는 마크다운)
        clean = text.replace("<b>", "**").replace("</b>", "**")
        r = requests.post(url, json={"content": clean, "allowed_mentions": {"parse": []}}, timeout=15)
        return r.ok
    except Exception as e:
        print("  Discord 전송 실패:", e)
        return False
```

복구 분기(`send_telegram("✅ ...복구...")` 줄) 바로 다음에 추가:
```python
        send_discord("✅ 파이프라인 복구 — 데이터 수집 정상화됨.")
```

stale 발송 분기(`if send_telegram(msg):` 블록) 안, `ALERT_STATE.write_text(...)` 다음 줄에 추가:
```python
        send_discord(msg)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest obsidian-automation/tests/test_watchdog_discord.py -v`
Expected: PASS (2 passed). 그리고 `python -m py_compile obsidian-automation/pipeline_watchdog.py` exit 0.

- [ ] **Step 5: 커밋 (obsidian-automation 레포)**

```bash
cd /c/Users/xect2/obsidian-automation
git add pipeline_watchdog.py tests/test_watchdog_discord.py
git commit -m "feat(watchdog): 파이프라인 stale/복구 Discord 발송(swing 채널) 병행"
```

---

### Task 7: 클라우드 트리거 30분 지연 + webhook env (설정)

**Files:**
- Modify: `C:/Users/xect2/obsidian-automation/gpt-api/vercel.json` (swing-trigger cron)
- Modify: `C:/Users/xect2/obsidian-automation/gpt-api/.env` (env 추가 — 로컬 비밀, 커밋 대상 아님)

**Interfaces:** 없음(설정).

- [ ] **Step 1: cron 5분→35분(09:35 KST)으로 변경**

`gpt-api/vercel.json`의:
```json
    { "path": "/api/swing-trigger", "schedule": "5 0 * * 1-5" }
```
→
```json
    { "path": "/api/swing-trigger", "schedule": "35 0 * * 1-5" }
```

- [ ] **Step 2: 워치독용 webhook env 추가**

`gpt-api/.env`에 한 줄 추가(값은 `swing-short-trading/.env`의 `SWING_DISCORD_WEBHOOK_URL` 복사):
```
SWING_DISCORD_WEBHOOK_URL=<swing-short-trading/.env의 값>
```

- [ ] **Step 3: 검증**

```bash
python -c "import json,sys; c=json.load(open(r'C:/Users/xect2/obsidian-automation/gpt-api/vercel.json')); print([x for x in c['crons'] if 'swing' in x['path']])"
```
Expected: `[{'path': '/api/swing-trigger', 'schedule': '35 0 * * 1-5'}]`

- [ ] **Step 4: 커밋 + 배포 (gpt-api 레포)**

```bash
cd /c/Users/xect2/obsidian-automation/gpt-api
git add vercel.json
git commit -m "chore(cron): swing-trigger 09:05→09:35 KST(로컬 페일오버 유예)"
```
(.env는 커밋하지 않음. Vercel 배포 시 cron 반영 — 기존 배포 파이프라인 따름.)

---

## 통합 검증 (전체 완료 후, CI 1회 수동)

- 마커에 us·kr 모두 오늘자로 채운 상태에서 `swing.yml` 수동 dispatch → 두 시장 skip, 커밋/Discord 없음(로그 "no-op").
- 마커에서 kr 제거 후 dispatch → kr만 run-once + review + brief + 페일오버 경고 Discord 1건.
- 워치독: `pipeline_heartbeat.txt`를 8시간 전으로 위조 → 텔레그램+Discord 둘 다 수신, 24h 내 재실행 시 dedup.

## Self-Review 메모(스펙 대비)
- 스펙 §2 마커 → Task1/2/4. §3 타이밍/가드 → Task5/7. §5 페일오버 경고 → Task3/5. §6 워치독 Discord → Task6. §7 충돌해소 → Task5 부수효과(코드 변경 없음, 하루 한쪽만 씀). §9 prereq(로컬 push 토큰·env) → Task4 주석/Task7. 모든 스펙 항목 매핑됨.
- 단순화: "KR brief 발송 성공까지" 엄격 게이팅 대신 run_once 정상 완료=성공으로 마커 기록(YAGNI; 실패는 드물고, 클라우드 중복은 안전측). 필요 시 후속 강화.
