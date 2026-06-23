"""거래 리뷰 — 거래 로그(체결)와 신호를 룰 기반으로 분석해 Review.md 생성.

LLM 호출은 선택(키 있으면 보강). 기본은 로컬 룰 기반.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..config import Config
from ..models import Order, Signal, SignalKind
from .learning_log import LearningLog


@dataclass
class TradeOutcome:
    ticker: str
    name: str
    entry_score: float | None
    realized_pnl: float
    return_pct: float | None
    stop_respected: bool
    note: str = ""


class TradeReviewer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.learning = LearningLog(cfg.state_dir)

    def review(self, signals: list[Signal], orders: list[Order], outcomes: list[TradeOutcome],
               d: date | None = None) -> str:
        d = d or date.today()
        buys = [o for o in orders if o.side == "BUY" and o.status == "filled"]
        sells = [o for o in orders if o.side == "SELL" and o.status == "filled"]
        wins = [o for o in outcomes if o.realized_pnl > 0]
        losses = [o for o in outcomes if o.realized_pnl < 0]
        win_rate = (len(wins) / len(outcomes) * 100) if outcomes else None
        avg_win = (sum(o.return_pct or 0 for o in wins) / len(wins)) if wins else None
        avg_loss = (sum(o.return_pct or 0 for o in losses) / len(losses)) if losses else None

        kept, broke = self._rule_audit(signals, orders, outcomes)

        # 실패 공통점 → 학습 룰
        for o in losses:
            if not o.stop_respected:
                self.learning.learn("stop_not_respected", "손절 미준수 거래는 큰 손실 — 손절 자동화 강제", o.name)
            if o.entry_score is not None and o.entry_score < 75:
                self.learning.learn("low_score_entry", "75점 미만 진입은 실패율 높음 — 진입 점수 상향", o.name)
        self.learning.save()

        kept_lines = [f"- {k}" for k in kept] or ["- —"]
        broke_lines = [f"- ⚠️ {b}" for b in broke] or ["- (없음)"]
        loss_lines = [
            f"- {o.name}: {o.return_pct:+.1f}% — "
            f"{o.note or ('손절 미준수' if not o.stop_respected else '룰 내 손실')}"
            for o in losses
        ] or ["- (손실 거래 없음)"]

        summary = (
            f"- 신호: BUY {sum(s.kind == SignalKind.BUY for s in signals)} · "
            f"WATCH {sum(s.kind == SignalKind.BUY_WATCH for s in signals)} · "
            f"BLOCKED {sum(s.kind == SignalKind.BLOCKED for s in signals)}"
        )
        lines = [
            "---", "type: 스윙리뷰", f"날짜: {d.isoformat()}", "tags: [스윙, 리뷰]", "---",
            f"# 🔎 매매 리뷰 · {d.isoformat()}",
            f"> 생성 {datetime.now():%Y-%m-%d %H:%M}", "",
            "## 오늘의 매매 요약",
            summary,
            f"- 체결: 매수 {len(buys)} · 매도 {len(sells)}",
            f"- 승률: {win_rate:.0f}%" if win_rate is not None else "- 승률: (청산 거래 없음)",
            f"- 평균 수익률: {avg_win:+.1f}%" if avg_win is not None else "- 평균 수익률: —",
            f"- 평균 손실률: {avg_loss:+.1f}%" if avg_loss is not None else "- 평균 손실률: —",
            "",
            "## 지킨 룰", *kept_lines,
            "",
            "## 어긴 룰 / 주의", *broke_lines,
            "",
            "## 실패 원인 분석", *loss_lines,
            "",
            "## 다음 개선 룰(누적 학습)",
            *self.learning.as_lines(),
            "",
            "## AI 재판단 메모",
            self._ai_or_rule_memo(outcomes),
        ]
        return "\n".join(lines)

    def _rule_audit(self, signals, orders, outcomes) -> tuple[list[str], list[str]]:
        kept, broke = [], []
        if all(o.stop_respected for o in outcomes):
            kept.append("모든 청산에서 손절 규칙 준수")
        else:
            broke.append("일부 거래에서 손절가 미준수")
        if not any(s.kind == SignalKind.BUY and s.event_risk.value == "높음" for s in signals):
            kept.append("이벤트 리스크 높은 종목 신규매수 회피")
        if any(o.entry_score is not None and o.entry_score < 70 and o.realized_pnl < 0 for o in outcomes):
            broke.append("70점 미만 진입에서 손실 발생 — 진입 기준 점검")
        return kept, broke

    def _ai_or_rule_memo(self, outcomes) -> str:
        # 기본: 룰 기반 요약. (LLM 보강은 키 있을 때 선택 — 데모는 룰 기반 유지)
        if not outcomes:
            return "- 청산 거래가 없어 결과 평가 보류. 신호/차단 사유는 Signals.md 참조."
        good = sum(o.realized_pnl > 0 for o in outcomes)
        return (f"- 청산 {len(outcomes)}건 중 {good}건 수익. "
                "점수와 결과의 상관을 다음 주 진입 임계값에 반영. "
                "손실 거래는 진입 점수·이벤트 타이밍·거래량 조건을 재점검.")
