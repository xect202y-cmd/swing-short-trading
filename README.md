# swing-short-trading — 단기 스윙 트레이딩 자동화 (페이퍼 기본)

100만원 시드로 하루~일주일 단기 스윙을 보조하는 **AI 룰 기반 자동화**.
좋은 장/좋은 타점에서만 +3~10%를 노리고, 나쁜 장에선 매매하지 않으며, 손실은 종목당 -2~3%로 제한한다.
**Hermes(투자상담/알림/크론) 시스템과 분리된 독립 테스트 프로그램이며, 그 파일을 수정하지 않는다.**

## ⚠️ 경고
- 투자에는 **원금 손실 위험**이 있다. 이 프로그램의 신호/주문은 참고용이며 수익을 보장하지 않는다.
- **기본은 페이퍼(모의) 트레이딩**(`PAPER_TRADING=true`)이다. 실주문은 절대 기본으로 실행되지 않는다.
- 실주문은 `PAPER_TRADING=false` + `LIVE_TRADING=true` + `I_UNDERSTAND_LIVE_RISK=true` 가 **모두** 켜져야만 가능하다(이중·삼중 보호).

## 설치
```bash
# uv (권장)
uv venv
uv pip install -e ".[dev]"          # 실데이터까지: ".[dev,market]"

# 또는 pip
python -m venv .venv && . .venv/Scripts/activate
pip install -e ".[dev]"
```

## 설정
1. `.env.example` → `.env` 복사 후 값 채우기. (또는 `C:\hermes\config\.env.swing`)
   - `OBSIDIAN_VAULT_ROOT` 를 **실제 볼트 경로**로. 기본값: `C:\Users\xect2\ObsidianVault\이용수_Wiki`
   - KIS 키는 선택(없으면 PaperBroker). `KIS_ENV=vps`(모의도메인) 권장.
   - **`.env` 는 절대 git 에 커밋하지 않는다.** API 키는 로그/마크다운에 출력되지 않는다(`[REDACTED]`).
2. `config.yaml` — 자금관리/손절익절/점수가중치/이벤트필터/경로 조정.

### .env.example 주요 항목
| 변수 | 설명 |
|---|---|
| `PAPER_TRADING` | 기본 true(모의). |
| `LIVE_TRADING` / `I_UNDERSTAND_LIVE_RISK` | 실주문 이중 보호 플래그. |
| `OBSIDIAN_VAULT_ROOT` | 볼트 루트(읽기/쓰기). |
| `KIS_APP_KEY/SECRET/ACCOUNT_NO/ENV` | 한국투자증권 KIS. |
| `DISCORD_WEBHOOK_URL` | 선택 알림(Hermes와 별도 웹훅 권장). |
| `OPENAI_API_KEY` | 선택 LLM 리뷰(기본은 룰 기반). |

## CLI
```bash
swing-trader doctor            # 환경/경로/키 점검
swing-trader scan              # 후보 점수화 → Signals.md
swing-trader run-once          # scan + 페이퍼 주문 → Trade.md
swing-trader review            # 거래 분석 → Review.md
swing-trader backtest --days 60  # 간단 백테스트 → Backtest.md
# 데모/테스트는 --limit 8 로 종목 수 제한 가능
```

## 옵시디언 폴더 구조
읽기: `금융뉴스/거시/📉 거시지표 대시보드.md`, `03_Ontology/Entities/Macro/📊 거시 국면.md`,
`금융뉴스/이벤트캘린더/📅 시장 이벤트 캘린더.md`, `03_Ontology/Rules/스윙_트레이딩_룰북.md`, `02_투자_유튜브/종목/*.md`
쓰기: `04_Trading/{Signals,Logs,Reviews,Backtests}/YYYY-MM-DD_*.md`

## 한국투자증권 API 키
1. KIS Developers 가입 → 앱 등록 → AppKey/AppSecret 발급.
2. **모의투자(VTS)** 계좌로 먼저 검증(`KIS_ENV=vps`).
3. `.env` 에 키/계좌 입력. 코드/로그에 키를 절대 남기지 않는다.

## 모의투자 실행 순서
```bash
swing-trader doctor      # 경로/키 확인
swing-trader scan        # 신호 확인(Signals.md)
swing-trader run-once    # PaperBroker 가상 체결(Trade.md)
swing-trader review      # 결과 분석(Review.md)
```

## 디스코드 알림 & 브리핑
`.env` 에 `SWING_DISCORD_WEBHOOK_URL` 설정 시 디스코드로 발송(없으면 콘솔/볼트만). **Hermes와 별도 채널/웹훅 권장.**
- **매수/매도 즉시 알림**: 종목·체결가·목표·손절·사유·점수·투입금액·손익비·가용현금
- **Daily 브리핑**(run-once 시): 계좌·누적수익율·승률·PF·MDD·보유종목 복기·오늘활동·차단사유·내일 주목
- **Weekly 브리핑**(금요일 자동): 주간 변화·점수대별 승률·룰준수율 등
- **Monthly 로직 보고서**(월 마지막 금요일 자동): 한 달 성과 + **로직 수정 제안**(임계값·손절폭·이벤트필터 등 구체 수치)

```bash
swing-trader brief --period auto      # 금=주간·월말=월간 자동
swing-trader brief --period weekly     # 수동
swing-trader brief --period monthly
```
산출물: `04_Trading/Briefs/{Daily,Weekly}.md`, `04_Trading/Reports/YYYY-MM_Logic.md`.
상태/지표: `state/{paper_state,equity_history,closed_trades}.json` (gitignore).

### 자동 스케줄 (시장 분리, 평일)
- **06:00 KST — `Swing-US`**: `run-once --market us --no-brief`. 미국 마감 데이터로 미국주 매매.
- **09:05 KST — `Swing-KR`**: `run-once --market kr`(전일 확정 캔들 판단 → 당일 시가 진입) + `review` + `brief --period auto`.
  - 금요일: Weekly 브리핑 + **백테스트(과거 검증)** 자동 실행·요약 포함
  - 월 마지막 금요일: Monthly **로직 수정 제안** 보고서
- Daily 브리핑은 아침 KR 런에서 1회(미국 포지션 포함 전체) 발송.
- 등록: `powershell -ExecutionPolicy Bypass -File _register_swing_task.ps1`

### 한 달 페이퍼 → 로직 개선 사이클
1. 매 평일 자동 페이퍼 — 매수/매도 알림 + Daily 브리핑(누적 성과)
2. 금요일 Weekly(+백테스트), 월말 Monthly **로직 수정 제안** 자동 보고
3. 한 달 후 Monthly 보고서의 제안을 `config.yaml`(점수 임계값·손절폭·익절·이벤트필터)에 반영
4. 반복하며 로직 정교화 → 검증되면 KIS 모의(VTS) → 소액 실전

## 실전 전환 체크리스트
- [ ] 모의(VTS)에서 최소 수 주 검증, 룰/손절 준수 확인
- [ ] `config.yaml` 자금/손절/한도 재확인
- [ ] `.env`: `PAPER_TRADING=false`, `LIVE_TRADING=true`, `I_UNDERSTAND_LIVE_RISK=true`
- [ ] `swing-trader doctor` 에서 "실전 주문 허용" ⚠️ 확인
- [ ] 소액부터, 1일/계좌 손실 한도 동작 확인

## 안전장치 (요약)
- 페이퍼 기본 + 실주문 삼중 플래그
- 1일 손실 -30,000원 / 계좌 -50,000원 한도 → 신규매매 중지
- 손절가 없는 매수 금지, 손익비 1.5 미만 금지, 예상손실 한도 초과 금지
- 동시 보유 2종목/종목당 50만원/이벤트 리스크 높음 시 매수 금지
- 자격증명은 로그/마크다운에 출력 금지(`[REDACTED]`), `.env` 미커밋

## 개발
```bash
uv run ruff check .
uv run pytest
```
> 시장 데이터: `pykrx`/`yfinance` 가 있으면 실데이터, 없으면 **결정적 합성 데이터**로 폴백(키/네트워크 없이 데모 가능, 실거래 성과 아님).
