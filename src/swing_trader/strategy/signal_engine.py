"""신호 엔진 — 노트+시장데이터+거시+이벤트를 종합해 Signal 생성."""
from __future__ import annotations

import logging

from ..config import Config
from ..macro.event_filter import EventState
from ..macro.regime import MacroState
from ..market.data_provider import DataProvider
from ..market.technicals import build_snapshot
from ..models import Signal, SignalKind, StockNote
from . import rationale, rules
from . import risk as risk_mod
from .methods import load_method_matcher
from .scoring import score_candidate

log = logging.getLogger(__name__)


class SignalEngine:
    def __init__(self, cfg: Config, provider: DataProvider):
        self.cfg = cfg
        self.provider = provider
        self.regime = None   # strategy.market_regime.Regime | None — v6 국면가변 추세필터
        self.matcher = load_method_matcher(cfg)
        self._ai_enabled = bool(cfg.creds.openai_api_key) and bool(cfg.get("ai", "enabled", default=True))
        self._ai_min = float(cfg.get("ai", "min_score", default=75))
        self._ai_veto = int(cfg.get("ai", "veto_confidence", default=70))
        if self._ai_enabled:
            log.info("AI 판단 활성(model=%s, 최소점수 %.0f)", cfg.creds.openai_model, self._ai_min)

    def evaluate(self, note: StockNote, macro: MacroState, events: EventState) -> Signal:
        ticker = note.ticker or ""
        name = note.display_name

        # 필수값 누락 → 즉시 BLOCKED 기록(조용히 넘기지 않음)
        if not ticker:
            return Signal(
                ticker="?", name=name, kind=SignalKind.BLOCKED, score=0.0, price=note.price or 0.0,
                blocked_reasons=["티커/종목코드 없음 — 시장데이터 조회 불가"] + note.missing,
            )

        df, source = self.provider.get_ohlcv(ticker)
        ind_cfg = self.cfg.get("indicators", default={})
        tech = build_snapshot(ticker, df, ind_cfg)

        ev_risk = events.market_risk()
        # 종목 리스크 가중(섹터가 임박 이벤트에 걸리면 상향)
        ev_risk = events.ticker_risk(f"{name} {note.memo or ''}", note.sector) or ev_risk

        weights = self.cfg.get("scoring", "weights", default={})
        min_tv = float(self.cfg.get("risk", "min_trading_value_eok", default=30))
        bd = score_candidate(note, tech, macro.risk, ev_risk, weights, min_tv)
        total = bd.total

        # 매매 기법(playbook) 매칭 → 부합 기법에 가점(네 기법이 곧 판단 렌즈)
        methods = self.matcher.match(df, tech)
        if methods:
            per = float(self.cfg.get("scoring", "method_bonus_per", default=5))
            cap = float(self.cfg.get("scoring", "method_bonus_max", default=12))
            total = min(100.0, round(total + min(len(methods) * per, cap), 1))

        reasons = rules.buy_reasons(note, tech)
        # 추세필터: 기본은 config(risk.require_uptrend). v6 라이브면 오늘 국면 정책값으로 대체
        # (BULL off·NEUTRAL/BEAR/CRASH on) — 백테스트 _v6_stock_trades 의 stock_up 게이트와 일치.
        require_uptrend = bool(self.cfg.get("risk", "require_uptrend", default=False))
        if self.regime is not None and str(self.cfg.get("regime", "logic_mode", default="v6")) == "v6":
            from .regime_policy import policy_for
            require_uptrend = policy_for(self.regime).require_uptrend
        momentum_min = float(self.cfg.get("risk", "momentum_min_pct", default=0.0))
        blocks = rules.buy_blocks(note, tech, macro.risk, ev_risk, min_tv,
                                  require_uptrend=require_uptrend, momentum_min_pct=momentum_min)

        # 목표/손절 산출 방식 — 고정/ATR/지지저항 후보 모두 계산(비교·저장), 채택값으로 플랜
        rk = self.cfg.get("risk", default={})
        take1 = float(rk.get("take1_pct", 5.0))
        stop_pct = float(rk.get("default_stop_pct", -3.0))
        tmethods = rationale.target_stop_methods(
            tech, note, take1, stop_pct,
            float(rk.get("atr_target_mult", 2.0)), float(rk.get("atr_stop_mult", 1.5)),
        )
        # 진입가: 노트 매수구간이 현재가의 ±50% 이내일 때만 사용(파싱오류 방어)
        bz = note.buy_zone1
        entry = bz if (bz and 0.5 <= bz / tech.price <= 1.5) else tech.price
        # 노트 값은 '스윙 범위'(손절<진입<목표 + 목표 ≤+15% + 손절 ≤-8%)일 때만 사용.
        # 애널리스트 컨센서스(장기 목표 +20%↑)·파싱오류는 무시하고 산출방식으로 폴백.
        note_ok = bool(
            note.stop and note.target1 and note.stop < entry < note.target1
            and (note.target1 - entry) / entry <= 0.15
            and (entry - note.stop) / entry <= 0.08
        )
        if note_ok:
            method_used, stop_v, target_v = "노트", note.stop, note.target1
        else:
            method_used = str(rk.get("target_stop_method", "고정비율"))
            cand = tmethods.get(method_used, tmethods["고정비율"])
            stop_v, target_v = cand["stop"], cand["target"]
        plan = risk_mod.build_plan(
            entry, stop=stop_v, target1=target_v, target2=note.target2,
            default_stop_pct=stop_pct, max_stop_pct=float(rk.get("max_stop_pct", -5.0)),
            take1_pct=take1, take2_pct=float(rk.get("take2_pct", 8.5)),
        )
        if plan.reward_risk is None or plan.reward_risk < float(rk.get("min_reward_risk", 1.5)):
            blocks.append(f"손익비 {plan.reward_risk} 부족(<{rk.get('min_reward_risk', 1.5)})")
        rtnl = rationale.entry_rationale(note, tech, macro.risk, ev_risk, methods, min_tv)

        th = self.cfg.get("scoring", "thresholds", default={})
        small_ok = float(th.get("small_ok", 70))

        if blocks:
            kind = SignalKind.BLOCKED
        elif total >= small_ok and reasons:
            kind = SignalKind.BUY
        elif total >= float(th.get("watch", 60)):
            kind = SignalKind.BUY_WATCH
        else:
            kind = SignalKind.HOLD

        method_reason = [f"🎯 기법 부합: {', '.join(methods)}"] if methods else []
        sig = Signal(
            ticker=ticker, name=name, kind=kind, score=total, price=tech.price,
            plan=plan if kind in (SignalKind.BUY, SignalKind.BUY_WATCH) else (plan if plan else None),
            macro_risk=macro.risk, event_risk=ev_risk, breakdown=bd,
            reasons=reasons + method_reason + [f"데이터 출처: {source}"],
            blocked_reasons=blocks, methods=methods,
            rationale=rtnl, target_methods=tmethods, target_method_used=method_used,
            atr_pct=tech.atr_pct, sector=note.sector,
        )

        # AI 최종 판단(키 있을 때만 · 상위 BUY 후보에 한해 호출 최소화 · 회피는 안전 veto)
        if kind == SignalKind.BUY and self._ai_enabled and total >= self._ai_min:
            from .ai_judge import judge
            mdicts = [m for m in self.matcher.methods if m.get("기법명") in methods]
            res = judge(self.cfg, sig, note, tech, mdicts)
            if res:
                sig.ai_verdict, sig.ai_reason, sig.ai_confidence = res["verdict"], res["reason"], res["confidence"]
                sig.reasons.append(f"🤖 AI {res['verdict']}({res['confidence']}): {res['reason']}")
                # 회피는 항상 보류. 관망은 확신 높을 때만 보류(낮으면 퀀트+기법 신호 유지).
                hold = res["verdict"] == "회피" or (res["verdict"] == "관망" and res["confidence"] >= self._ai_veto)
                if hold:
                    sig.kind = SignalKind.BUY_WATCH
                    sig.blocked_reasons.append(f"AI {res['verdict']}({res['confidence']}) — 매수 보류")
        return sig

    def scan(self, notes: list[StockNote], macro: MacroState, events: EventState) -> list[Signal]:
        out: list[Signal] = []
        for n in notes:
            try:
                out.append(self.evaluate(n, macro, events))
            except Exception as e:  # noqa: BLE001 — 실패도 신호로 남김
                log.warning("평가 실패 %s: %s", n.display_name, e)
                out.append(Signal(
                    ticker=n.ticker or "?", name=n.display_name, kind=SignalKind.BLOCKED,
                    score=0.0, price=n.price or 0.0, blocked_reasons=[f"평가 오류: {e}"],
                ))
        return out
