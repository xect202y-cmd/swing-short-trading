"""v6 전략 문서 렌더러 — v6_compare payload → 04_Trading/Logic/<날짜>_v6.md (12항목)."""
from __future__ import annotations

from ..strategy.regime_policy import V6_POLICY


def _edge_row(v):
    e = v["edge"]
    return (f"| {v['label']} | {e.get('n_trades')} | {e.get('total_return_pct')}% | "
            f"{e.get('cagr_pct')}% | {e.get('mdd_pct')}% | {e.get('sharpe')} | "
            f"{e.get('sortino')} | {e.get('calmar')} | {e.get('win_rate')}% | "
            f"{e.get('avg_win_pct')}% | {e.get('avg_loss_pct')}% | {e.get('profit_factor')} | "
            f"{e.get('expectancy_pct')}% | {e.get('realized_rr')} | {e.get('avg_hold_days')} | "
            f"{e.get('max_consec_losses')} |")


def render_v6_doc(compare: dict, policy: dict | None = None) -> str:
    policy = policy or V6_POLICY
    d = compare.get("as_of")
    L = ["---", "type: 스윙로직", "버전: v6", f"날짜: {d}", "tags: [스윙, 로직, v6, regime]", "---",
         f"# 🧭 v6 하이브리드 regime 스윙 · {d}",
         f"> 유니버스 백테스트 OOS {compare.get('oos_start')}~{compare.get('oos_end')} · "
         f"시드 {compare.get('seed'):,.0f} · lookback {compare.get('lookback_days')}일", ""]

    L += ["## 1. 전략 개요",
          "v5의 유연한 진입·우수한 손익비·부분익절+트레일링을 유지하되, v4의 추세 가드레일을 "
          "market regime(BULL/NEUTRAL/BEAR/CRASH)별로 가변 적용해 약세장 리스크를 줄이는 하이브리드.", ""]

    L += ["## 2. v4/v5 대비 변경점",
          "- v4: 추세필터 항상 ON(약세 방어 강, 강세 기회 손실).",
          "- v5: 추세필터 OFF(강세 유연, 약세 무방비).",
          "- **v6: regime별 가변** — BULL은 v5처럼 유연, BEAR/CRASH는 v4 이상으로 보수적"
          "(+CRASH 차단·사이징 축소·트레일 타이트).", ""]

    L += ["## 3. market_regime 판별 기준 (지수: KR 코스피 ^KS11 / US S&P500 ^GSPC)",
          "- CRASH: 60일 고점대비 낙폭 ≤ -12% 또는 5일 수익률 ≤ -8%",
          "- BEAR: 종가 < 200일선 그리고 50일선 하락(20일 기울기<0)",
          "- BULL: 종가 > 200일선 그리고 50일선 > 200일선 그리고 종가 > 50일선",
          "- NEUTRAL: 그 외 (룩어헤드 없음 — t 시점까지 데이터만)", ""]

    L += ["## 4. regime별 설정값",
          "| regime | 추세필터 | 신규진입 | 트레일% | risk/trade% | max_stop(라이브) | ai_min(라이브) | RR(라이브) |",
          "|---|---|---|---|---|---|---|---|"]
    for reg, p in policy.items():
        L.append(f"| {reg.value} | {'ON' if p.require_uptrend else 'OFF'} | "
                 f"{'차단' if p.block_new_entry else '허용'} | {p.trail_pct} | "
                 f"{p.risk_per_trade_pct} | {p.max_stop_pct} | {p.ai_min_score} | {p.min_reward_risk} |")
    L += ["", "> ⚠️ max_stop·ai_min·RR은 라이브 전용 게이트(백테스트 미반영 — 과거 AI점수/구조손절 없음).", ""]

    L += ["## 5. 진입 조건",
          "기본: 20일선 눌림 후 반등 + 거래대금 하한. regime 게이트: BULL 추세무관, "
          "NEUTRAL/BEAR 종목 정배열(종가>60일선 AND 20>60) 필요, CRASH 차단(예외조건만).", ""]

    L += ["## 6. 차단 조건 (사유 로깅 → decision_log)",
          "- CRASH 예외 미충족 / ai_score·reward_risk regime기준 미만 / 손절폭 regime캡 초과 / "
          "포트폴리오·섹터 한도 초과 / 유동성 부족 / 이벤트리스크 과도 / 시장·섹터·종목 동반 하락 / "
          "과열 추격 / 기대값 낮음. 각 차단은 (종목·사유·근거값) 기록.", ""]

    L += ["## 7. 손절/익절/트레일링",
          "- 익절 v5 유지: 1차 6% 절반 익절 → 잔량 트레일링으로 2차 8.5%.",
          "- 트레일링 regime 가변(BULL 3.0 → CRASH 1.5) — 약세일수록 타이트.",
          "- 손절 default -2.5, max_stop 캡 regime별(라이브). -7%는 BULL+조건 전부 충족 시만.", ""]

    L += ["## 8. 포지션 사이징",
          "risk_per_trade regime별(1.0/0.75/0.5/0.25%). 백테스트 자산곡선=거래별 "
          "risk_per_trade/|손절폭|. 엣지비교는 고정분율 0.2로 사이징 중립.", ""]

    L += ["## 9. v4/v5/v6 백테스트 비교표 (동일 종목·기간·비용·체결, 고정분율 0.2 엣지)",
          "| 버전 | 거래 | 총수익 | CAGR | MDD | Sharpe | Sortino | Calmar | 승률 | 평균익 | 평균손 | PF | 기대값 | 실현RR | 평균보유 | 최대연속손실 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    L += [_edge_row(v) for v in compare.get("versions", [])]
    L += ["", "### 실전형(regime 가변 사이징) 자산곡선",
          "| 버전 | 총수익 | CAGR | MDD | Calmar |", "|---|---|---|---|---|"]
    for v in compare.get("versions", []):
        a = v["as_traded"]
        L.append(f"| {v['label']} | {a.get('total_return_pct')}% | {a.get('cagr_pct')}% | "
                 f"{a.get('mdd_pct')}% | {a.get('calmar')} |")
    L += ["", "> ⚠️ 자산곡선은 거래 순차 전액복리 모델이라 총수익/CAGR **절대값은 과장**"
          "(동시보유·중복 미반영). 상대비교·MDD·Calmar만 유효. 거래당 엣지(위 표)가 1차 판단근거.", ""]

    L += ["## 10. regime별 성과표 (v6, 엣지)"]
    v6 = next((v for v in compare.get("versions", []) if v["label"] == "v6"), None)
    if v6:
        L += ["| regime | 거래수 | 수익률 | MDD |", "|---|---|---|---|"]
        for reg, s in (v6["edge"].get("by_regime") or {}).items():
            L.append(f"| {reg} | {s['n']} | {s['ret_pct']}% | {s['mdd_pct']}% |")
    L.append("")

    L += ["## 11. v5 진입 but v6 차단 거래 사후검증 (반사실)"]
    cf = compare.get("counterfactual", {})
    if cf.get("n"):
        L += [f"- 차단 {cf['n']}건 · 평균 사후수익 {cf.get('avg_ret_pct')}% · "
              f"손실회피 기여율 {cf.get('helped_pct')}%(사후수익≤0 비율).",
              "| 차단사유 | 건수 | 평균 사후수익 |", "|---|---|---|"]
        for reason, s in (cf.get("by_reason") or {}).items():
            L.append(f"| {reason} | {s['n']} | {s['avg_ret_pct']}% |")
        L += ["", "> 평균 사후수익<0면 차단이 손실회피에 기여, >0면 좋은 기회 과차단.",
              "> ⚠️ 백테스트 표본이 강세·V반등 구간이면 급락도 회복되어 '차단이 손해'로 보일 수 있음"
              "(MDD 축소가 v6의 본질 가치 — 지속 약세장에서 발현)."]
    else:
        L.append("- 반사실 차단 없음.")
    L.append("")

    L += ["## 12. 추가 개선안",
          "- regime 임계값 OOS 튜닝(-12%/-8% 등).",
          "- 섹터 지수 이력 확보 시 섹터 추세 가드레일 백테스트 편입.",
          "- 라이브 score/RR 게이트 페이퍼 실적 누적 후 regime 임계 재보정.",
          "- CRASH 예외 진입의 실측 성과 추적(현재 라이브 전용).", ""]
    return "\n".join(L) + "\n"
