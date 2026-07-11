"""파이프라인 — scan / run-once / review / backtest / doctor.

각 단계는 '왜 매수/매도/차단했는지'를 마크다운에 남긴다.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .broker.paper import PaperBroker
from .config import Config, load_config, redact
from .execution.order_manager import OrderManager
from .execution.position_manager import PositionManager
from .macro.event_filter import parse_events
from .macro.regime import assess_macro
from .market.data_provider import DataProvider
from .models import Order, Signal
from .obsidian.reader import VaultReader
from .obsidian.writer import VaultWriter
from .review.trade_reviewer import TradeOutcome, TradeReviewer
from .state import daily_marker as _DM
from .strategy.signal_engine import SignalEngine

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _provider(cfg: Config) -> DataProvider:
    md = cfg.get("market_data", default={})
    from .market.fx import get_usdkrw
    fx = get_usdkrw(float(md.get("fx_usdkrw", 1400)))   # 실시간 환율(실패 시 config 폴백)
    return DataProvider(provider=md.get("provider", "auto"), lookback_days=int(md.get("lookback_days", 120)),
                        fx_usdkrw=fx)


def _is_kr_ticker(ticker: str | None) -> bool:
    c = (ticker or "").split(".")[0]
    return c.isdigit() and len(c) == 6


def _load_notes(cfg: Config, reader: VaultReader, limit: int | None, market: str = "all"):
    """종목노트 로드 + 이름→코드 맵 해석 + 시장 필터(kr/us/all).

    market='kr'  → 한국(6자리 코드) 종목만 (아침 실행: 당일 시가 진입)
    market='us'  → 미국(영문 티커) 종목만 (새벽 실행: 미국 마감 데이터)
    """
    from .obsidian.ticker_map import build_ticker_map
    notes = reader.stock_notes(limit=None)          # 전체 로드 후 해석/필터(맵 커버리지 ↑)
    tmap = build_ticker_map(notes, cfg.state_dir)
    resolved = 0
    for n in notes:
        if not n.ticker:
            code = tmap.resolve(n.name)
            if code:
                n.ticker = code
                n.missing = [m for m in n.missing if "티커" not in m]
                resolved += 1
    if market in ("kr", "us"):
        want_kr = market == "kr"
        notes = [n for n in notes if n.ticker and _is_kr_ticker(n.ticker) == want_kr]
    if limit:
        notes = notes[:limit]
    log.info("티커 해석: 맵 %d개 · 추가 %d개 · 시장 '%s' → %d종목", len(tmap), resolved, market, len(notes))
    return notes


def _build(cfg: Config):
    reader = VaultReader(cfg)
    provider = _provider(cfg)
    macro = assess_macro(
        reader.macro_dashboard(), reader.macro_regime(),
        vix_caution=float(cfg.get("event_filter", "vix_caution", default=20.0)),
    )
    events = parse_events(
        reader.event_calendar(), _DM.today_kst(),
        block_keywords=cfg.get("event_filter", "block_keywords", default=["FOMC", "CPI", "PCE"]),
        window_days=int(cfg.get("event_filter", "block_window_days", default=2)),
    )
    engine = SignalEngine(cfg, provider)
    writer = VaultWriter(cfg)
    return reader, provider, macro, events, engine, writer


def _save_decision_log(cfg: Config, signals: list[Signal], result, market: str) -> None:
    """분석용 의사결정 로그 — 왜 샀나/왜 차단했나/점수분해/목표방식/순위를 모두 저장.

    state/decision_log/YYYY-MM-DD_<market>.json → 나중에 종목별/조건별 성과 분석.
    """
    order_blocks = {s.ticker: rs for s, rs in result.blocked}

    def rec(s: Signal) -> dict:
        return {
            "ticker": s.ticker, "name": s.name, "kind": s.kind.value, "score": s.score,
            "rank": s.rank, "sector": s.sector, "atr_pct": s.atr_pct,
            "breakdown": [{"name": i.name, "score": i.score, "max": i.max_score, "reason": i.reason}
                          for i in (s.breakdown.items if s.breakdown else [])],
            "rationale": s.rationale, "target_methods": s.target_methods,
            "target_method_used": s.target_method_used,
            "plan": ({"entry": s.plan.entry, "stop": s.plan.stop, "target1": s.plan.target1,
                      "target2": s.plan.target2, "rr": s.plan.reward_risk} if s.plan else None),
            "methods": s.methods, "ai_verdict": s.ai_verdict, "ai_confidence": s.ai_confidence,
            "ai_reason": s.ai_reason,
            "block_reasons": (s.blocked_reasons or []) + order_blocks.get(s.ticker, []),
        }

    cands = [s for s in signals if s.rank] or [s for s in signals if s.score > 0]
    payload = {"date": _DM.today_kst().isoformat(), "market": market,
               "candidates": [rec(s) for s in sorted(cands, key=lambda x: x.rank or 999)]}
    ddir = cfg.state_dir / "decision_log"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / f"{_DM.today_kst().isoformat()}_{market}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_run(cfg: Config, signals: list[Signal], orders: list[Order]) -> dict:
    """last_run.json = '오늘(KST)의 누적 원장'. 같은 날짜면 이전 런(us→kr)과 병합.

    하루 다회 실행에서 마지막 런이 앞선 런의 체결을 덮어쓰면 review/Daily 가
    '매도 0'으로 집계되는 버그(2026-07-02)의 근본 수정. 병합된 payload 를 반환해
    run_once 의 '오늘 활동' 집계도 같은 소스를 쓴다."""
    def ser(o):
        d = asdict(o) if is_dataclass(o) else dict(o)
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        d["stop"] = getattr(o, "stop", None)
        d["target"] = getattr(o, "target", None)
        return d

    today = _DM.today_kst().isoformat()
    new_orders = [ser(o) for o in orders]
    new_signals = [{"ticker": s.ticker, "name": s.name, "kind": s.kind.value,
                    "score": s.score, "event_risk": s.event_risk.value} for s in signals]
    path = cfg.state_dir / "last_run.json"
    prev: dict = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    if prev.get("date") == today:
        new_ids = {o.get("order_id") for o in new_orders}
        new_orders = [o for o in prev.get("orders", []) if o.get("order_id") not in new_ids] + new_orders
        new_tickers = {s.get("ticker") for s in new_signals}
        new_signals = [s for s in prev.get("signals", []) if s.get("ticker") not in new_tickers] + new_signals
    payload = {"date": today, "orders": new_orders, "signals": new_signals}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _day_counts(payload: dict) -> tuple[int, int]:
    """오늘 누적 원장에서 체결(filled) 매수/매도 건수 — Trade.md 행과 1:1."""
    filled = [o for o in payload.get("orders", []) if o.get("status") == "filled"]
    return (sum(o.get("side") == "BUY" for o in filled),
            sum(o.get("side") == "SELL" for o in filled))


# ── 명령 ──
def run_scan(cfg: Config, limit: int | None = None, market: str = "all") -> tuple[list[Signal], Path]:
    reader, provider, macro, events, engine, writer = _build(cfg)
    notes = _load_notes(cfg, reader, limit, market)
    signals = engine.scan(notes, macro, events)
    path = writer.write_signals(signals)
    log.info("scan: 노트 %d개 → 신호 %d개 (BUY %d) → %s",
             len(notes), len(signals), sum(s.kind.value == "BUY" for s in signals), path)
    return signals, path


def run_once(cfg: Config, limit: int | None = None, market: str = "all", do_brief: bool = True) -> dict:
    reader, provider, macro, events, engine, writer = _build(cfg)
    notes = _load_notes(cfg, reader, limit, market)
    # v6 라이브 regime — 오늘 시장국면을 시장지수로 판별(ma200 위해 긴 히스토리 fetch).
    # 시장별 지수가 명확한 kr/us 단일 시장 런에서만 적용(all 은 US를 KOSPI로 오분류 방지 위해 미적용).
    # engine.scan(추세필터)·OrderManager(점수/RR/차단) 양쪽에 같은 국면 전달.
    regime = None
    if bool(cfg.get("regime", "enabled", default=False)) and market in ("kr", "us"):
        from .strategy import market_regime as _MR
        try:
            series = _regime_series_for_market(cfg, market, 5)
            regime = list(series.values())[-1] if series else _MR.Regime.NEUTRAL
            log.info("오늘 시장국면[%s] = %s", market, regime.value)
        except Exception:  # noqa: BLE001 — 지수 조회 실패 시 보수적 NEUTRAL(조용히 넘기지 않고 경고)
            regime = _MR.Regime.NEUTRAL
            log.warning("regime 지수 조회 실패[%s] → NEUTRAL 폴백", market)
    engine.regime = regime
    signals = engine.scan(notes, macro, events)
    sig_path = writer.write_signals(signals)

    # 데이터 수집 건전성 점검(브로커가 보유종목으로 sources 오염시키기 전 스냅샷).
    # 종목노트 0건·전부 synthetic 폴백이면 조용히 넘기지 않고 Swing 디스코드로 ⚠️ 경고.
    from .notify import health as _H
    _hz = _H.assess([provider.sources.get(n.ticker) for n in notes])
    if not _hz.ok:
        _H.alert(cfg.creds.discord_webhook_url, f"일일 스캔[{market}]", _hz.reason)

    broker = PaperBroker(
        seed_cash=float(cfg.get("capital", "seed", default=1_000_000)),
        state_path=cfg.state_dir / "paper_state.json",
        price_fn=lambda s: provider.get_ohlcv(s)[0]["close"].iloc[-1] if s else None,
        fee_bps=float(cfg.get("paper", "fee_bps", default=1.5)),
        slippage_bps=float(cfg.get("paper", "slippage_bps", default=5.0)),
    )
    # 0) 거래일 경과 → 보유일수 +1 (멀티데이 스윙: '5거래일 초과 후 매도' 로직 활성화)
    advanced = broker.advance_bar(_DM.today_kst().isoformat())
    # 1) 보유 청산 점검 먼저
    pm = PositionManager(cfg, broker, provider)
    exits = pm.check_and_exit()
    # 2) 신규 매수(안전장치)
    # 일/주 손실 한도는 '그 날/그 주' 실현손익만 봐야 함(broker.realized_pnl 은 전체 누적이라
    # 오래전 손실이 매일 '1일 손실 한도'를 영구히 걸어 봇이 안 사는 버그). closed_trades.json 의
    # exit_date 창으로 산출 + 이번 런 청산(아직 미저장)을 extra 로 합산.
    from datetime import timedelta
    from .review import analytics as _A
    _closed_now = [c for _, _, c in exits]
    _today = _DM.today_kst()
    _week_start = (_today - timedelta(days=_today.weekday())).isoformat()
    realized_today = _A.realized_since(cfg.state_dir, _today.isoformat(), _closed_now)
    realized_week = _A.realized_since(cfg.state_dir, _week_start, _closed_now)
    om = OrderManager(cfg, broker, realized_today=realized_today, realized_total=realized_week,
                      regime=regime)   # regime 은 스캔 전에 산출됨(위)
    result = om.execute_signals(signals)
    broker.save()

    # 청산 거래 원장 적재(분석/브리핑용)
    from .notify.discord import notify_embeds
    from .review import briefer as _B
    closed = _closed_now
    _A.record_closed_trades(cfg.state_dir, closed)
    # 분석용 의사결정 로그(왜 샀나/차단했나/점수분해/목표방식/순위) + 차단·WATCH 사후추적
    _save_decision_log(cfg, signals, result, market)
    from .review import tracker as _T
    _T.record_and_update(cfg, provider, signals, advanced)

    # 즉시 알림(매수/매도) → 디스코드 임베드(카드/표, KR🇰🇷·US🇺🇸 구분)
    wh = cfg.creds.discord_webhook_url
    sig_map = {s.ticker: s for s in signals if s.ticker}
    from .market.fx import get_usdkrw
    buy_embed, buy_md = _B.trade_buy_alert(result.placed, broker.get_cash_balance(),
                                           len(broker.get_positions()), sig_map,
                                           get_usdkrw(float(cfg.get("market_data", "fx_usdkrw", default=1400))))
    if buy_embed:
        notify_embeds(wh, [buy_embed], buy_md)
    sell_embed, sell_md = _B.trade_sell_alert(closed)
    if sell_embed:
        notify_embeds(wh, [sell_embed], sell_md)

    # 오늘 누적 원장(us→kr 병합) 먼저 갱신 — Daily/Review 집계의 단일 소스.
    all_orders = [o for o, _, _ in exits] + result.placed
    day_run = _save_run(cfg, signals, all_orders)
    day_bought, day_sold = _day_counts(day_run)

    # Daily 브리핑(성과+보유복기 표) → 디스코드 + 볼트 기록.
    # 시장 분리 실행 시 마지막(아침 한국) 런에서만 1회 발송(do_brief=False면 건너뜀).
    # '오늘 활동'은 이 런이 아니라 오늘 전체(앞선 US 런 포함) 기준 — Trade.md 행과 일치.
    if do_brief:
        activity = {"bought": day_bought, "sold": day_sold, "blocked": result.blocked}
        daily_embed, daily_md = _B.daily_brief(cfg, broker, provider, signals, activity)
        notify_embeds(wh, [daily_embed], daily_md)
        writer.write_daily(daily_md)
    else:
        # 브리핑 생략(US 런)이어도 대시보드용 open_positions.json 은 항상 갱신 — 매도/매수 즉시 앱 반영.
        # (daily_brief 가 내부에서 _positions_data 로 저장하므로 do_brief 시엔 중복 불필요.)
        _B._positions_data(cfg, broker, provider)

    # 빈 날에도 Trade.md 를 남겨 '왜 매매하지 않았는지'를 기록(조용히 넘기지 않는다).
    trade_path = writer.append_trades(all_orders)
    if not day_run["orders"]:   # 오늘 하루 전체 기준(앞선 런이 체결했으면 문구 생략)
        n_buy = sum(s.kind.value == "BUY" for s in signals)
        with trade_path.open("a", encoding="utf-8") as f:
            f.write(f"\n> 오늘 신규 체결 없음 — 매수신호 {n_buy}건 · 차단 {len(result.blocked)}건. "
                    "보수적 회피(나쁜 타점엔 매매하지 않음). 상세 사유는 Signals.md 참조.\n")

    log.info("run-once: 매수 %d · 매도 %d · 차단 %d (오늘 누적 매수 %d · 매도 %d)",
             len(result.placed), len(exits), len(result.blocked), day_bought, day_sold)
    # 페일오버 마커: 이 시장 런이 정상 완료됨을 기록(클라우드가 읽어 중복 방지)
    _DM.record_done(cfg.state_dir, market, datetime.now(_DM.KST))
    return {
        "signals": sig_path, "trades": trade_path,
        "bought": len(result.placed), "sold": len(exits), "blocked": result.blocked,
        "cash": broker.get_cash_balance(), "realized": broker.realized_pnl,
    }


def _is_last_friday(d: date) -> bool:
    from datetime import timedelta
    return d.weekday() == 4 and (d + timedelta(days=7)).month != d.month


def run_brief(cfg: Config, period: str = "auto") -> list[str]:
    """주간/월간 브리핑 발송+기록. auto: 금요일=주간, 월 마지막 금요일=월간. daily는 run-once에 포함."""
    from datetime import date as _date

    from .notify.discord import notify_embeds
    from .review import briefer as _B
    provider = _provider(cfg)
    broker = PaperBroker(
        seed_cash=float(cfg.get("capital", "seed", default=1_000_000)),
        state_path=cfg.state_dir / "paper_state.json",
        price_fn=lambda s: provider.get_ohlcv(s)[0]["close"].iloc[-1] if s else None,
    )
    writer = VaultWriter(cfg)
    wh = cfg.creds.discord_webhook_url
    today = _DM.today_kst()
    sent: list[str] = []

    if period == "daily":
        embed, md = _B.daily_brief(cfg, broker, provider, [], {"bought": 0, "sold": 0, "blocked": []})
        notify_embeds(wh, [embed], md)
        writer.write_daily(md)
        sent.append("daily")
    if period == "weekly" or (period == "auto" and today.weekday() == 4):
        # 주 1회 백테스트(과거 검증) 자동 실행 → Backtest.md 기록 + 주간 브리핑에 요약 포함.
        # 수집 실패(예외·노트 0건·전부 synthetic)는 조용히 누락하지 않고 Swing 디스코드로 ⚠️ 경고.
        from .notify import health as _H
        from .obsidian.reader import VaultReader
        from .strategy import backtest as _BT
        notes = [n for n in VaultReader(cfg).stock_notes(limit=15) if n.ticker]
        bt_summary = None
        try:
            rows, bt_summary, take, stop = _BT.simulate(cfg, provider, notes, 60)
            writer.write_backtest(_BT.render_md(rows, 60, take, stop, today.isoformat()))
            hz = _H.assess([provider.sources.get(n.ticker) for n in notes])
            if not hz.ok:
                _H.alert(wh, "주간 백테스트", hz.reason)
        except Exception as e:  # noqa: BLE001 — 백테스트가 죽어도 주간 브리핑은 계속, 대신 경고
            log.exception("주간 백테스트 실패")
            _H.alert(wh, "주간 백테스트", f"시뮬레이션 예외: {type(e).__name__}: {e}")
        embed, md = _B.weekly_brief(cfg, broker, provider, bt_summary)
        notify_embeds(wh, [embed], md)
        writer.write_weekly(md)
        sent.append("weekly")
        # 주간에 AI 로직 진단도 자동 포함(매매일지·로그 근거 → 로직 문제점+수정안)
        from .notify.discord import notify
        from .review import analytics as _A
        from .review import logic_reviewer as _LR
        lr_discord, lr_md, lr_state = _LR.run(cfg)
        writer.write_logic_review(lr_md)
        _A.record_logic_review(cfg.state_dir, lr_state)
        if lr_discord:
            notify(wh, lr_discord)
        sent.append("logic-review")
        # 주간 검증 하니스(IS/OOS 측정) — 옵시디언·디스코드·대시보드(harness_latest.json) 자동 갱신.
        try:
            run_harness(cfg)
            sent.append("harness")
            run_version_compare(cfg)   # 버전비교 데이터(version_compare.json)도 갱신
            run_scalp_compare(cfg)     # 단타 카테고리 비교 데이터도 주간 갱신
        except Exception as e:  # noqa: BLE001 — 하니스 실패해도 나머지 브리핑 영향 없음, 대신 경고
            log.exception("주간 하니스 실패")
            _H.alert(wh, "주간 하니스", f"예외: {type(e).__name__}: {e}")
    if period == "monthly" or (period == "auto" and _is_last_friday(today)):
        embed, md = _B.monthly_report(cfg, broker, provider)
        notify_embeds(wh, [embed], md)
        writer.write_monthly(md)
        sent.append("monthly")
    log.info("brief(%s): 발송 %s", period, sent or "없음(해당일 아님)")
    return sent


def run_review(cfg: Config) -> Path:
    writer = VaultWriter(cfg)
    reviewer = TradeReviewer(cfg)
    last = cfg.state_dir / "last_run.json"
    signals: list[Signal] = []
    orders: list[Order] = []
    outcomes: list[TradeOutcome] = []
    if last.exists():
        data = json.loads(last.read_text(encoding="utf-8"))
        from .models import RiskLevel, SignalKind
        for s in data.get("signals", []):
            try:
                signals.append(Signal(
                    ticker=s.get("ticker", "?"), name=s.get("name", "?"),
                    kind=SignalKind(s.get("kind", "HOLD")), score=s.get("score", 0.0),
                    price=0.0, event_risk=RiskLevel(s.get("event_risk", "낮음")),
                ))
            except (ValueError, KeyError):
                continue
        for o in data.get("orders", []):
            orders.append(Order(
                order_id=o["order_id"], ticker=o["ticker"], side=o["side"], quantity=o["quantity"],
                price=o["price"], order_type=o.get("order_type", "limit"), status=o.get("status", ""),
                filled_price=o.get("filled_price"), fee=o.get("fee", 0.0), slippage=o.get("slippage", 0.0),
                note=o.get("note", ""),
            ))
        # 매도 체결 → 결과(데모: note 의 실현손익 파싱)
        for o in orders:
            if o.side == "SELL" and o.status == "filled":
                pnl = 0.0
                if "실현손익" in (o.note or ""):
                    import re
                    m = re.search(r"(-?\d[\d,]*)원", o.note)
                    if m:
                        pnl = float(m.group(1).replace(",", ""))
                ret = (pnl / (o.filled_price or o.price or 1) / max(o.quantity, 1)) * 100
                outcomes.append(TradeOutcome(
                    ticker=o.ticker, name=o.ticker, entry_score=None, realized_pnl=pnl,
                    return_pct=round(ret, 2), stop_respected=pnl >= 0 or "손절" in (o.note or ""),
                    note=o.note,
                ))
    content = reviewer.review(signals, orders, outcomes)
    path = writer.write_review(content)
    log.info("review → %s", path)
    return path


def run_backtest(cfg: Config, days: int = 60, limit: int | None = 8) -> Path:
    """간단 백테스트 — 최근 days 데이터에서 '20일선 눌림 후 익절/손절' 단순 규칙 성과."""
    from .strategy import backtest as _BT
    reader, provider, macro, events, engine, writer = _build(cfg)
    notes = [n for n in reader.stock_notes(limit=limit) if n.ticker]
    rows, summary, take, stop = _BT.simulate(cfg, provider, notes, days)
    path = writer.write_backtest(_BT.render_md(rows, days, take, stop, _DM.today_kst().isoformat()))
    log.info("backtest → %s (종목 %d·거래 %d·평균승률 %s)",
             path, summary.n_stocks, summary.total_trades, summary.avg_win_rate)
    from .notify import health as _H
    hz = _H.assess([provider.sources.get(n.ticker) for n in notes])
    if not hz.ok:
        _H.alert(cfg.creds.discord_webhook_url, f"백테스트({days}일)", hz.reason)
    return path


def run_logic(cfg: Config, note: str) -> dict:
    """로직 버전 스냅샷 + 이전과 diff + A/B 백테스트 → 옵시디언 변경이력 기록."""
    from .notify.discord import notify
    from .strategy import backtest as _BT
    from .strategy import logic_version as _LV
    reader, provider, macro, events, engine, writer = _build(cfg)
    new = _LV.snapshot(cfg)
    versions = _LV.load_versions(cfg.state_dir)
    prev = versions[-1]["snapshot"] if versions else None
    prev_v = versions[-1]["version"] if versions else None
    changes = _LV.diff(prev, new)
    ab = None
    runner_ab = None
    notes = [n for n in _load_notes(cfg, reader, 20, "kr") if n.ticker]
    if prev:
        _, old_s, _, _ = _BT.simulate(cfg, provider, notes, 60,
                                      take_pct=prev.get("risk.take1_pct"), stop_pct=prev.get("risk.default_stop_pct"))
        _, new_s, _, _ = _BT.simulate(cfg, provider, notes, 60,
                                      take_pct=new.get("risk.take1_pct"), stop_pct=new.get("risk.default_stop_pct"))
        ab = (old_s, new_s)
    # 승자를 달리게 A/B (현재 설정 · 전량익절 vs 부분익절+트레일링)
    _, full_s, _, _ = _BT.simulate(cfg, provider, notes, 60, runner=False)
    _, run_s, _, _ = _BT.simulate(cfg, provider, notes, 60, runner=True)
    runner_ab = (full_s, run_s)
    vnum = _LV.save_version(cfg.state_dir, new, note)
    path = writer.write_logic(_LV.render(vnum, note, changes, ab, prev_v, runner_ab), vnum)
    writer.append_logic_changelog(_LV.changelog_line(vnum, note, changes, ab))
    msg = f"🔧 **로직 v{vnum}** — {note}\n변경 {len(changes)}건"
    if ab and ab[0].avg_win_rate is not None and ab[1].avg_win_rate is not None:
        msg += f" · 백테스트 승률 {ab[0].avg_win_rate}%→{ab[1].avg_win_rate}%"
    notify(cfg.creds.discord_webhook_url, msg)
    log.info("logic v%d 기록 → %s (변경 %d건)", vnum, path, len(changes))
    return {"version": vnum, "path": path, "changes": changes, "ab": ab, "prev_v": prev_v}


def run_logic_review(cfg: Config) -> dict:
    """AI 로직 진단 — 매매일지·의사결정로그·성과를 LLM에 주고 로직 문제점+수정안 도출 → 볼트 기록."""
    from .notify.discord import notify
    from .review import analytics as _A
    from .review import logic_reviewer as _LR
    writer = VaultWriter(cfg)
    discord, md, state = _LR.run(cfg)
    path = writer.write_logic_review(md)
    _A.record_logic_review(cfg.state_dir, state)
    if discord:
        notify(cfg.creds.discord_webhook_url, discord)
    log.info("logic-review → %s (발송 %s)", path, bool(discord))
    return {"path": path, "sent": bool(discord)}


def run_doctor(cfg: Config) -> dict:
    reader = VaultReader(cfg)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("볼트 루트 존재", cfg.vault_root.exists(), str(cfg.vault_root)))
    for key in ("macro_dashboard", "macro_regime", "event_calendar", "rulebook"):
        p = cfg.read_path(key)
        checks.append((f"읽기: {key}", p.exists(), str(p)))
    notes = reader.stock_note_paths()
    checks.append(("종목 노트", len(notes) > 0, f"{len(notes)}개 발견"))
    for key in ("logs_dir", "signals_dir", "reviews_dir", "backtests_dir"):
        p = cfg.write_dir(key)
        checks.append((f"쓰기 폴더: {key}", True, f"{p} (필요 시 자동 생성)"))
    # 자격증명(존재 여부만, 값은 redact)
    checks.append(("KIS 키", cfg.creds.has_kis, redact(cfg.creds.kis_app_key)))
    checks.append(("Discord 웹훅", bool(cfg.creds.discord_webhook_url), redact(cfg.creds.discord_webhook_url)))
    checks.append(("OpenAI 키", bool(cfg.creds.openai_api_key), redact(cfg.creds.openai_api_key)))
    # 안전 상태
    s = cfg.safety
    checks.append(("PAPER_TRADING", s.paper_trading, str(s.paper_trading)))
    checks.append(("실전 주문 허용 여부", s.live_allowed,
                   "허용(주의!)" if s.live_allowed else "차단됨(안전)"))
    return {"checks": checks, "live_allowed": s.live_allowed}


def run_harness(cfg: Config) -> Path:
    """현재 로직 baseline 을 full 유니버스·장기 히스토리로 IS/OOS 측정 → 볼트+디스코드."""
    from .strategy import harness as _HN
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    market = str(cfg.get("backtest", "universe", default="all"))
    notes = [n for n in _load_notes(cfg, reader, None, market) if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    trades = _HN.simulate_trades(cfg, provider, notes, days)
    is_t, oos_t = _HN.split_oos(trades, float(cfg.get("backtest", "oos_fraction", default=0.3)))
    pf = float(cfg.get("backtest", "position_frac", default=0.2))   # MDD 복리곡선의 거래당 자본분율
    is_rep, oos_rep = _HN.report_from_trades(is_t, pf), _HN.report_from_trades(oos_t, pf)
    writer = VaultWriter(cfg)
    ver = _HN.logic_version_id(cfg)   # 결과를 현재 로직 버전으로 태깅(나중 버전별 A/B 키) → 옵시디언에 무조건 기록
    md = _HN.render_report_md("기준 로직 성과 측정", is_rep, oos_rep, _DM.today_kst().isoformat(), version=ver)
    path = writer.write_harness(md)
    # 대시보드용 compact JSON(헤르메스 대시보드가 readSwingState 로 GitHub raw 읽음). 버전 누적 시 A/B 화면도 이걸로.
    gap = (round(oos_rep.expectancy - is_rep.expectancy, 3)
           if oos_rep.expectancy is not None and is_rep.expectancy is not None else None)
    (cfg.state_dir / "harness_latest.json").write_text(json.dumps({
        "version": ver, "date": _DM.today_kst().isoformat(), "n_stocks": len(notes),
        "position_frac": pf, "is": asdict(is_rep), "oos": asdict(oos_rep), "gap": gap,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    from .notify import health as _H
    hz = _H.assess([provider.sources.get(n.ticker) for n in notes])
    if not hz.ok:
        _H.alert(cfg.creds.discord_webhook_url, "하니스 측정", hz.reason)
    from .notify.discord import notify
    floor = int(cfg.get("backtest", "min_oos_trades", default=100))
    guard = "" if oos_rep.n_trades >= floor else f" ⚠️표본부족(OOS {oos_rep.n_trades}<{floor})"
    notify(cfg.creds.discord_webhook_url,
           f"🧪 하니스 측정 — 로직 `{ver}` · 종목 {len(notes)} · OOS 거래 {oos_rep.n_trades} · "
           f"기대값 IS {_HN._fmt(is_rep.expectancy)}→OOS {_HN._fmt(oos_rep.expectancy)} · "
           f"MDD {_HN._fmt(oos_rep.max_drawdown)}{guard}")
    log.info("harness → %s (종목 %d · OOS거래 %d · OOS기대값 %s)",
             path, len(notes), oos_rep.n_trades, oos_rep.expectancy)
    return path


def _params_from_snapshot(snap: dict, cfg: Config) -> dict:
    """logic_versions 스냅샷(평탄 dict) → _stock_trades 파라미터(과거 버전 리플레이용)."""
    def g(k, d):
        v = snap.get(f"risk.{k}")
        return d if v is None else v
    take = float(g("take1_pct", 5.0)) / 100
    stop = float(g("default_stop_pct", -3.0)) / 100
    take2 = float(g("take2_pct", 8.5)) / 100
    trail = float(g("trail_pct", 3.0))
    max_hold = int(g("max_hold_days", 20))
    partial = float(g("partial_exit_pct", 0.5) or 0)
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    return {"take": take, "stop": stop, "take2": take2, "trail": trail, "max_hold": max_hold,
            "runner": partial > 0, "require_uptrend": bool(g("require_uptrend", False)),
            "cost": 2 * (fee + slip), "min_tv_eok": float(g("min_trading_value_eok", 30)),
            "momentum_min_pct": float(g("momentum_min_pct", 0.0) or 0.0)}


def _core_logic(p: dict) -> list[str]:
    """스냅샷 파라미터 → 사람이 읽는 핵심로직 불릿."""
    return [
        "상승배열(>60일선·정배열)에서만 눌림목 매수" if p["require_uptrend"] else "20일선 눌림목 매수(추세 무관)",
        f"+{p['take']*100:.0f}% 익절 / {p['stop']*100:.1f}% 손절",
        "절반 익절 후 트레일링(승자 달리게)" if p["runner"] else "전량 익절",
        f"진입 후 최대 {p['max_hold']}거래일 보유(미래 {p['max_hold']}일 내 익절/손절 평가)",
    ]


# 버전 제목 — "어떤 로직인지" 대시보드에 바로 보이게(노트 접두어 대신 정제 문구).
_VERSION_TITLES = {
    1: "베이스라인 · 추세무관 눌림목",
    2: "승자 달리게 · 부분익절+트레일",
    3: "청산 튜닝 · 손절-2.5%/익절6%",
    4: "추세필터 · 정배열에서만 진입",
    5: "유연진입 · 추세필터 off·RR1.75",
    6: "하이브리드 regime · 국면별 가변방어",
    7: "추세추종 청산 · 5일선/대량음봉까지 홀딩(마스터스윙)",
    8: "무한매수법 · 눌림 진입 후 정액DCA+평단10%익절(라오어)",
    9: "추세추종 + 모멘텀 진입필터 · 60일선 대비 +5%↑ 강세만(v7 개선)",
}


def _version_title(vnum, note) -> str:
    if vnum in _VERSION_TITLES:
        return _VERSION_TITLES[vnum]
    note = (note or "").strip()
    return (note.split(":")[0] if ":" in note else note)[:40] or f"버전 {vnum}"


def _core_logic_v6() -> list[str]:
    return [
        "시장국면별 가변: BULL 유연 · NEUTRAL/BEAR 추세필터 · CRASH 신규차단",
        "+6% 익절 / -2.5% 손절 (부분익절 후 국면별 트레일)",
        "국면별 사이징(risk 1.0~0.25%) · 라이브 점수/손익비 게이트 가변",
        "진입 후 최대 20거래일 보유",
    ]


def _core_logic_v7() -> list[str]:
    return [
        "강세(정배열: 종가>60일선·20>60일선) 종목만 — 신고가 강세주 프록시",
        "20일선 눌림 후 반등에 진입(v5 검증 눌림 타점 재사용)",
        "익절 고정 없이 5일선 이탈까지 홀딩 — 승자 달리게(v5 +6% 고정익절이 승자 자르던 문제 해소)",
        "대량거래량 음봉 = 세력 이탈 신호로 매도 · 손절 -3%",
    ]


def _core_logic_v8() -> list[str]:
    return [
        "진입은 v7 그대로 — 강세(정배열) 종목의 20일선 눌림 반등",
        "진입 후 무한매수법(라오어): 매일 종가 1분할 정액 DCA — 하락하면 사서 평단↓",
        "평단 대비 +10% 도달 시 전량 익절 → 사이클 리셋 (손절 없음)",
        "분할 소진 후 사이클 상한일 미도달 시 종가 청산 (백테스트 유한성)",
    ]


def _v7v9_portfolio(dfs: dict, notes: list, *, stop, max_hold, cost, min_tv,
                    seed: float = 5_000_000.0, maxpos: int = 3) -> dict:
    """최대 maxpos종목·seed 실계좌 리플레이 — v7 vs v9(모멘텀 +5%) 실현수익·MDD. 슬롯 제한 시 우열."""
    from .strategy import backtest as _BT

    def _signals(mom_thr):
        sig, price = [], {}
        for n in notes:
            df = dfs.get(n.ticker)
            if df is None or len(df) < 70:
                continue
            c = _BT._col(df, "close").to_numpy(dtype=float); o = _BT._col(df, "open").to_numpy(dtype=float)
            v = _BT._col(df, "volume").to_numpy(dtype=float)
            ma5 = _BT._col(df, "close").rolling(5, min_periods=1).mean().to_numpy(dtype=float)
            ma20 = _BT._col(df, "close").rolling(20, min_periods=1).mean().to_numpy(dtype=float)
            ma60 = _BT._col(df, "close").rolling(60, min_periods=1).mean().to_numpy(dtype=float)
            va20 = _BT._col(df, "volume").rolling(20, min_periods=5).mean().to_numpy(dtype=float)
            dts = [d.strftime("%Y-%m-%d") for d in df.index]
            price[n.ticker] = dict(zip(dts, c))
            i, nn = 20, len(c)
            while i < nn - 2:
                up = c[i] > ma60[i] and ma20[i] > ma60[i]
                mom = (c[i] / ma60[i] - 1) if ma60[i] > 0 else 0
                if (c[i] <= ma20[i] * 1.01 and c[i] > c[i - 1] and c[i] * v[i] / 1e8 >= min_tv
                        and o[i + 1] > 0 and up and (mom_thr <= 0 or mom * 100 >= mom_thr)):
                    ret, jend = _BT._v7_exit(c, o, v, ma5, va20, i + 1, o[i + 1], stop=stop,
                                             take1=None, volspike=2.5, max_hold=max_hold)
                    sig.append((dts[i + 1], dts[jend], float(ret) - cost, mom, n.ticker, float(o[i + 1])))
                    i = max(jend, i + 1)
                i += 1
        return sig, price

    def _replay(mom_thr):
        sig, price = _signals(mom_thr)
        by_entry: dict = {}
        for s in sig:
            by_entry.setdefault(s[0], []).append(s)
        all_dates = sorted({d for tk in price for d in price[tk]})
        cash, openpos, curve = seed, [], []
        for d in all_dates:
            openpos2 = []
            for pos in openpos:
                if pos["exit_date"] == d:
                    cash += pos["alloc"] * (1 + pos["ret"])
                else:
                    openpos2.append(pos)
            openpos = openpos2
            equity = cash + sum(pos["alloc"] * (price[pos["ticker"]].get(d, pos["entry_price"]) / pos["entry_price"]) for pos in openpos)
            held = {pos["ticker"] for pos in openpos}
            for (ed, xd, ret, mom, tk, epx) in sorted(by_entry.get(d, []), key=lambda s: s[3], reverse=True):
                if len(openpos) >= maxpos or tk in held:
                    continue
                alloc = min(cash, equity / maxpos)
                if alloc < 1:
                    continue
                cash -= alloc
                openpos.append({"ticker": tk, "alloc": alloc, "entry_price": epx, "exit_date": xd, "ret": ret})
                held.add(tk)
            curve.append(cash + sum(pos["alloc"] * (price[pos["ticker"]].get(d, pos["entry_price"]) / pos["entry_price"]) for pos in openpos))
        if not curve:
            return {"cum_pct": None, "mdd_pct": None, "n_signals": len(sig)}
        peak = curve[0]; mdd = 0.0
        for e in curve:
            peak = max(peak, e); mdd = min(mdd, e / peak - 1)
        return {"cum_pct": round((curve[-1] / seed - 1) * 100, 1), "mdd_pct": round(mdd * 100, 1), "n_signals": len(sig)}

    return {"seed": seed, "maxpos": maxpos, "v7": _replay(0.0), "v9": _replay(5.0)}


def run_version_compare(cfg: Config) -> Path:
    """각 로직 버전을 백테스트 리플레이 → 가상 시드계좌 OOS 곡선+핵심로직 → state/version_compare.json.

    대시보드 버전비교 화면용. v6(regime.logic_mode=v6)는 국면가변 엔진(_v6_stock_trades)으로
    충실 재생성(지수 국면 시계열 반영), 나머지는 고정 파라미터 리플레이."""
    from .strategy import backtest as _BT
    from .strategy import harness as _HN
    from .strategy import logic_version as _LV
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    notes = [n for n in _load_notes(cfg, reader, None, str(cfg.get("backtest", "universe", default="all")))
             if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    frac = float(cfg.get("backtest", "oos_fraction", default=0.3))
    pfrac = float(cfg.get("backtest", "position_frac", default=0.2))
    seed = float(cfg.get("capital", "seed", default=5_000_000))
    dfs = {n.ticker: provider.get_ohlcv(n.ticker)[0].tail(days) for n in notes}

    reg_maps: dict | None = None   # v6 있을 때만 지수 국면 시계열 lazy fetch {kr,us}
    out = []
    for v in _LV.load_versions(cfg.state_dir):
        vnum = v.get("version")
        snap = v.get("snapshot", {})
        is_v6 = str(snap.get("regime.logic_mode") or "") == "v6"
        is_v7 = str(snap.get("regime.logic_mode") or "") == "v7"
        is_v8 = str(snap.get("regime.logic_mode") or "") == "v8"
        p = _params_from_snapshot(snap, cfg)
        trades = []
        if is_v8:
            # v8 파라미터는 스냅샷의 v8.* 키에서(divisions·사이클상한·익절). 없으면 기본값.
            for n in notes:
                for d, r in _BT._v8_stock_trades(
                        dfs[n.ticker], take=float(snap.get("v8.take_pct", 10.0)) / 100,
                        divisions=int(snap.get("v8.divisions", 40)),
                        max_cycle_days=int(snap.get("v8.max_cycle_days", 60)),
                        cost=p["cost"], min_tv_eok=p["min_tv_eok"], require_uptrend=True):
                    trades.append(_HN.Trade(n.ticker, d, r))
        elif is_v7:
            for n in notes:
                for d, r in _BT._v7_stock_trades(
                        dfs[n.ticker], stop=p["stop"], take1=None, volspike=2.5,
                        max_hold=p["max_hold"], cost=p["cost"], min_tv_eok=p["min_tv_eok"],
                        require_uptrend=True, momentum_min_pct=p["momentum_min_pct"]):
                    trades.append(_HN.Trade(n.ticker, d, r))
        elif is_v6:
            if reg_maps is None:
                reg_maps = {}
                for mk in ("kr", "us"):
                    try:
                        reg_maps[mk] = _regime_series_for_market(cfg, mk, days)
                    except Exception:  # noqa: BLE001
                        log.warning("version_compare regime 지수 조회 실패: %s", mk)
                        reg_maps[mk] = {}
            for n in notes:
                reg = reg_maps.get(_market_of_ticker(n.ticker), {})
                for d, r, _rg, _hd in _BT._v6_stock_trades(
                        dfs[n.ticker], reg, take=p["take"], default_stop=p["stop"],
                        take2=p["take2"], cost=p["cost"], min_tv_eok=p["min_tv_eok"]):
                    trades.append(_HN.Trade(n.ticker, d, r))
        else:
            for n in notes:
                for d, r in _BT._stock_trades(dfs[n.ticker], take=p["take"], stop=p["stop"],
                                              max_hold=p["max_hold"], runner=p["runner"], take2=p["take2"],
                                              trail=p["trail"], cost=p["cost"], min_tv_eok=p["min_tv_eok"],
                                              require_uptrend=p["require_uptrend"]):
                    trades.append(_HN.Trade(n.ticker, d, r))
        _is, oos = _HN.split_oos(trades, frac)
        rep = _HN.report_from_trades(oos, pfrac)
        eq, curve = seed, []
        for t in sorted(oos, key=lambda t: t.entry):
            eq *= (1 + pfrac * t.ret)
            curve.append({"date": t.entry, "equity": round(eq)})
        core = _core_logic_v8() if is_v8 else (_core_logic_v7() if is_v7 else (_core_logic_v6() if is_v6 else _core_logic(p)))
        if is_v7 and p.get("momentum_min_pct", 0) > 0:   # v9 = v7 + 모멘텀 진입필터
            core = list(core) + [f"진입 품질필터: 종가 60일선 대비 +{p['momentum_min_pct']:.0f}%↑ 강세만 진입 — 약한 진입 제거(승률·PF·MDD 개선)"]
        out.append({
            "label": f"v{vnum}",
            "title": _version_title(vnum, v.get("note")),
            "core_logic": core,
            "oos": {"expectancy": rep.expectancy, "profit_factor": rep.profit_factor,
                    "max_drawdown": rep.max_drawdown, "sharpe": rep.sharpe, "win_rate": rep.win_rate,
                    "n_trades": rep.n_trades, "cum_return_pct": round((eq / seed - 1) * 100, 2) if oos else None},
            "equity": curve,
        })
    # OOS 검증기간(곡선 커버 범위) — 버전 통합 최소~최대 진입일.
    dates = [pt["date"] for v in out for pt in v["equity"]]
    oos_start, oos_end = (min(dates), max(dates)) if dates else (None, None)
    oos_days = (date.fromisoformat(oos_end) - date.fromisoformat(oos_start)).days if dates else None
    lookback = int(cfg.get("backtest", "lookback_days", default=500))
    path = cfg.state_dir / "version_compare.json"
    adopted = str(cfg.get("regime", "adopted_version", default="") or "").strip() or None
    # 최대 3종목 실계좌 리플레이(v7 vs v9) — 슬롯 제한 시 우열(무제한 누적과 뒤집힘) 증명용.
    try:
        _fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
        _slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
        portfolio_replay = _v7v9_portfolio(
            dfs, notes, stop=float(cfg.get("risk", "default_stop_pct", default=-3.0)) / 100,
            max_hold=int(cfg.get("risk", "max_hold_days", default=40)), cost=2 * (_fee + _slip),
            min_tv=float(cfg.get("risk", "min_trading_value_eok", default=30)), seed=seed)
    except Exception as e:  # noqa: BLE001
        log.warning("portfolio_replay 실패: %s", e)
        portfolio_replay = None
    path.write_text(json.dumps({
        "as_of": _DM.today_kst().isoformat(), "seed": seed,
        "oos_start": oos_start, "oos_end": oos_end, "oos_days": oos_days, "lookback_days": lookback,
        "adopted": adopted,   # 실전 채택 버전(대시보드 '채택됨' 배지)
        "versions": out,
        "portfolio_replay": portfolio_replay,   # 최대3종목 실계좌 v7 vs v9
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("version_compare → %s (버전 %d개, OOS %s~%s, 채택 %s)", path, len(out), oos_start, oos_end, adopted)
    return path


def _regime_index_ticker(market: str) -> str:
    return {"kr": "^KS11", "us": "^GSPC"}.get(market, "^KS11")


def _regime_series_for_market(cfg: Config, market: str, usable_bars: int) -> dict:
    """regime 판별용 지수 시계열 — ma200 워밍업(220봉) 확보 위해 usable_bars 보다 길게 fetch.

    라이브 provider(lookback 120)로 지수를 받으면 ma200 이 전부 NaN → BULL/BEAR 판별 불가
    (국면이 사실상 CRASH/NEUTRAL로만 붕괴). 전용 긴 히스토리 provider로 방지한다.
    """
    from .market.data_provider import DataProvider
    from .market.fx import get_usdkrw
    from .strategy import market_regime as _MR
    md = cfg.get("market_data", default={})
    prov = DataProvider(provider=md.get("provider", "auto"),
                        lookback_days=int(usable_bars) + 220,
                        fx_usdkrw=get_usdkrw(float(md.get("fx_usdkrw", 1400))))
    idf, _ = prov.get_ohlcv(_regime_index_ticker(market))
    return _MR.classify_series(
        idf, crash_dd=float(cfg.get("regime", "crash_dd", default=-0.12)),
        crash_ret5=float(cfg.get("regime", "crash_ret5", default=-0.08)))


def _market_of_ticker(ticker: str) -> str:
    """KR 종목코드는 숫자로 시작(005930, 005935.KS). 그 외(AVGO)는 US."""
    return "kr" if ticker and ticker[0].isdigit() else "us"


def run_v6_compare(cfg: Config) -> Path:
    """v4/v5/v6 동일조건 백테스트 + regime별 + 반사실 → state/v6_compare.json + 볼트 문서."""
    from .review.v6_doc import render_v6_doc
    from .strategy import backtest as _BT
    from .strategy import harness as _HN
    from .strategy import market_regime as _MR
    from .strategy import metrics as _MX
    from .strategy.regime_policy import V6_POLICY
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    notes = [n for n in _load_notes(cfg, reader, None, str(cfg.get("backtest", "universe", default="all")))
             if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    seed = float(cfg.get("capital", "seed", default=5_000_000))
    fee = float(cfg.get("paper", "fee_bps", default=1.5)) / 10000
    slip = float(cfg.get("paper", "slippage_bps", default=5.0)) / 10000
    cost = 2 * (fee + slip)
    min_tv = float(cfg.get("risk", "min_trading_value_eok", default=30))
    take, dstop, take2 = 0.06, -0.025, 0.085
    dfs = {n.ticker: provider.get_ohlcv(n.ticker)[0].tail(days) for n in notes}
    # regime 시계열: 시장(kr/us)별 지수를 trade 창(days)+ma200 워밍업만큼 길게 조회 →
    # trade 창 전 구간에서 BULL/BEAR 판별 가능(워밍업이 창을 NEUTRAL로 오염시키지 않음).
    reg_by_market: dict = {}
    for mk in ("kr", "us"):
        try:
            reg_by_market[mk] = _regime_series_for_market(cfg, mk, days)
        except Exception:  # noqa: BLE001 — 지수 조회 실패 시 NEUTRAL 폴백
            log.warning("regime 지수 조회 실패: %s", mk)
            reg_by_market[mk] = {}

    def _reg_tag(reg_map, d):
        return reg_map.get(d, _MR.Regime.NEUTRAL).value

    # v4(추세필터 on)/v5(off) 고정 파라미터 거래 — regime 태그는 진입일 지수로 부여
    def _fixed(require_uptrend):
        recs = []
        for n in notes:
            reg = reg_by_market.get(_market_of_ticker(n.ticker), {})
            for d, r in _BT._stock_trades(dfs[n.ticker], take=take, stop=dstop, max_hold=20,
                                          runner=True, take2=take2, trail=3.0, cost=cost,
                                          min_tv_eok=min_tv, require_uptrend=require_uptrend):
                recs.append(_MX.TradeRec(d, r, _reg_tag(reg, d), 0))
        return recs

    v4 = _fixed(True)
    v5 = _fixed(False)
    v6: list = []
    blocks: list = []
    for n in notes:
        reg = reg_by_market.get(_market_of_ticker(n.ticker), {})
        t, b = _BT._v6_entries_and_blocks(dfs[n.ticker], reg, take=take, default_stop=dstop,
                                          take2=take2, cost=cost, min_tv_eok=min_tv)
        v6 += [_MX.TradeRec(e, r, rg, hd) for (e, r, rg, hd) in t]
        blocks += b

    # 사이징: risk_per_trade% / |손절폭%| = 자본분율. v4/v5는 flat 1.0%, v6는 regime 가변.
    frac = {r.value: p.risk_per_trade_pct / abs(dstop * 100) for r, p in V6_POLICY.items()}
    flat = {k: 1.0 / abs(dstop * 100) for k in ("BULL", "NEUTRAL", "BEAR", "CRASH")}
    versions = [
        {"label": "v4", "title": "추세필터 고정 ON",
         "edge": _MX.full_report(v4, fixed_frac=0.2),
         "as_traded": _MX.full_report(v4, frac_by_regime=flat)},
        {"label": "v5", "title": "추세필터 OFF(유연)",
         "edge": _MX.full_report(v5, fixed_frac=0.2),
         "as_traded": _MX.full_report(v5, frac_by_regime=flat)},
        {"label": "v6", "title": "regime 가변 하이브리드",
         "edge": _MX.full_report(v6, fixed_frac=0.2),
         "as_traded": _MX.full_report(v6, frac_by_regime=frac)},
    ]
    # 반사실 요약(v5 진입 O·v6 차단 O)
    cf: dict = {"n": len(blocks)}
    if blocks:
        avg = sum(b["ret_v5"] for b in blocks) / len(blocks)
        helped = sum(1 for b in blocks if b["ret_v5"] <= 0) / len(blocks) * 100
        by_reason = {}
        for reason in {b["reason"] for b in blocks}:
            sub = [b for b in blocks if b["reason"] == reason]
            by_reason[reason] = {"n": len(sub),
                                 "avg_ret_pct": round(sum(x["ret_v5"] for x in sub) / len(sub) * 100, 3)}
        cf.update({"avg_ret_pct": round(avg * 100, 3), "helped_pct": round(helped, 1),
                   "by_reason": by_reason})
    all_dates = [t.entry for v in (v4, v5, v6) for t in v]
    path = cfg.state_dir / "v6_compare.json"
    payload = {
        "as_of": _DM.today_kst().isoformat(), "seed": seed,
        "oos_start": min(all_dates) if all_dates else None,
        "oos_end": max(all_dates) if all_dates else None,
        "lookback_days": days, "versions": versions, "counterfactual": cf,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    VaultWriter(cfg).write_logic(render_v6_doc(payload), 6)   # → 04_Trading/Logic/<날짜>_v6.md
    log.info("v6_compare → %s (v4 %d·v5 %d·v6 %d 거래, 차단 %d)",
             path, len(v4), len(v5), len(v6), len(blocks))
    return path


def run_scalp_compare(cfg: Config) -> Path:
    """단타 v1/v2 백테스트 리플레이 → state/scalp_compare.json (대시보드 카테고리 비교)."""
    from .scalp import backtest as _SB
    from .scalp.account import SEED_PER_MODEL
    from .strategy import harness as _HN
    reader = VaultReader(cfg)
    provider = _HN.backtest_provider(cfg)
    notes = [n for n in _load_notes(cfg, reader, None,
                                    str(cfg.get("backtest", "universe", default="all"))) if n.ticker]
    days = int(cfg.get("backtest", "lookback_days", default=500))
    frac = float(cfg.get("backtest", "oos_fraction", default=0.3))
    min_tv = float(cfg.get("scalp", "min_tv_eok", default=50))
    pfrac = 1.0 / 5   # 모델당 5분할 사이징과 동일 프레임
    seed = float(SEED_PER_MODEL)

    trades: dict = {"v1": [], "v2": [], "v3": [], "v4": [], "v5": [], "v6": []}
    for n in notes:
        try:
            df, _src = provider.get_ohlcv(n.ticker)
        except Exception:  # noqa: BLE001
            continue
        r = _SB.simulate_stock(n.ticker, df.tail(days), min_tv_eok=min_tv)
        for m in trades:
            trades[m] += r.get(m, [])

    # v5 오버나잇 상따는 전시장 패널(FinanceDataReader 캐시, krx_universe.fetch_panel 로
    # 사전 수집)이 정본. 패널이 없으면(예: CI) 빈 결과 + 라벨로 정직하게 표기 —
    # 관심종목 유니버스로 돌리면 다른 전략을 같은 이름으로 비교하게 되므로 폴백하지 않는다.
    v5_universe = "패널 없음(전시장 캐시 필요)"
    try:
        from .scalp.krx_universe import load_cache, v5_market_trades, v6_market_trades
        panel = {k: v for k, v in load_cache(cfg.state_dir).items() if v is not None}
        if len(panel) >= 500:
            trades["v5"] = v5_market_trades(panel, min_tv_eok=min_tv)
            trades["v6"] = v6_market_trades(panel, min_tv_eok=min_tv)   # v5 + 코스닥 국면게이트
            v5_universe = f"전시장 {len(panel)}"
    except Exception as e:  # noqa: BLE001
        log.warning("v5/v6 전시장 패널 사용 불가: %s", e)

    meta = {"v1": ("단타 v1", "변동성 돌파(추세형)",
                   ["당일 시가+0.5×전일레인지 돌파 시 매수", "당일 종가 전량 청산(오버나잇 없음)",
                    "장중 손절 -2.0%(저가 터치 보수 판정)", "거래대금 하한 필터"]),
            "v2": ("단타 v2", "갭하락 과매도 반등(역추세형)",
                   ["시가 -2% 이상 갭하락 + 전일 20>60일선 종목 시가 매수",
                    "당일 종가 전량 청산(오버나잇 없음)", "장중 손절 -2.5%(보수 판정)"]),
            "v3": ("단타 v3", "터닝포인트 갭반등 · 손익비 RR7 (불장단타왕 기법 반영)",
                   ["시가 -2% 이상 갭하락 + 전일 20>60일선 종목 시가 매수(v2 골격)",
                    "리턴 구간에서만: 50일선 흐름 비하락 + 종가가 VWMA50(거래량 가중 지지) 위",
                    "손익비 원칙: 장중 손절 -1.0% / 익절 +7% (동시 터치 시 손절 우선 보수 판정)",
                    "당일 종가 전량 청산(오버나잇 없음)"]),
            "v4": ("단타 v4", "급등 후 첫 조정 반등 (CIS 순행·Aziz ABCD·강창권 눌림목 수렴)",
                   ["정배열 강세주가 전일 +5%↑ 급등 → 당일 갭하락(-2%) 조정에 시가 매수",
                    "추격 금지·눌림목 매수: '급등 후 반드시 눌림목이 온다, 그때 매수'(강창권)",
                    "손익비 원칙: 장중 손절 -1.5% / 익절 +10% (동시 터치 시 손절 우선 보수 판정)",
                    "당일 종가 전량 청산(오버나잇 없음)"]),
            "v5": ("단타 v5", f"오버나잇 상따 — 폭등 종가 매수→익일 시가 매도 (이가근 상한가 따라잡기) · 유니버스 {v5_universe}",
                   ["당일 +15%↑ 폭등(상한가 잠금 29.5%↑ 마감은 매수 불가라 제외) + 거래량 20일평균×5↑ + 60일 신고가",
                    "종가 매수(동시호가 참여 가정) → 익일 시가 매도 — 상따 수익의 본체인 오버나잇 갭만 취함",
                    "인트라데이 변형(익일 시가 매수)은 OOS 644건 -76%로 폐기 — 그 시가가 상따의 매도 지점",
                    "백테스트 전용(라이브 채택 시 15시 장중 스캔 런 필요) · 거래대금 50억↑"]),
            "v6": ("단타 v6", f"v5 오버나잇 상따 + 코스닥 50일선 국면게이트 (2026-07-08 채택) · 유니버스 {v5_universe}",
                   ["v5 셋업 동일 — 당일 +15%↑ 폭등 + 거래량 20일평균×5↑ + 60일 신고가 → 종가매수·익일시가청산",
                    "코스닥(KQ11)이 50일선 아래(약세 국면)면 신규 진입 보류 — 하락장 손실거래 제거",
                    "검증(2026-07-08): 무필터 대비 PF 1.19→1.27·위험조정 2.69→4.09, IS·OOS 양쪽 개선, MA 30~60 견고",
                    "라이브: 15시 스캔 런에서 게이트 판정(정산·청산은 국면 무관 유지)"])}
    trades = {m: [t for t in ts if math.isfinite(t.ret)] for m, ts in trades.items()}  # 비유한 수익률 제거
    out = []
    for m in ("v1", "v2", "v3", "v4", "v5", "v6"):
        _is, oos = _HN.split_oos(trades[m], frac)
        rep = _HN.report_from_trades(oos, pfrac)
        eq, curve = seed, []
        for t in sorted(oos, key=lambda t: t.entry):
            eq *= (1 + pfrac * t.ret)
            curve.append({"date": t.entry, "equity": round(eq)})
        label, title, core = meta[m]
        out.append({"label": label, "title": title, "core_logic": core,
                    "oos": {"expectancy": rep.expectancy, "profit_factor": rep.profit_factor,
                            "max_drawdown": rep.max_drawdown, "sharpe": rep.sharpe,
                            "win_rate": rep.win_rate, "n_trades": rep.n_trades,
                            "cum_return_pct": round((eq / seed - 1) * 100, 2) if oos else None},
                    "equity": curve})
    dates = [pt["date"] for v in out for pt in v["equity"]]
    oos_start, oos_end = (min(dates), max(dates)) if dates else (None, None)
    oos_days = ((date.fromisoformat(oos_end) - date.fromisoformat(oos_start)).days
                if dates else None)
    adopted = str(cfg.get("scalp", "adopted_version", default="") or "").strip() or None
    if adopted and not adopted.startswith("단타"):
        adopted = f"단타 {adopted}"   # versions[].label("단타 v2")과 매칭되는 형태로 정규화
    path = cfg.state_dir / "scalp_compare.json"
    path.write_text(json.dumps({
        "as_of": _DM.today_kst().isoformat(), "seed": seed,
        "oos_start": oos_start, "oos_end": oos_end, "oos_days": oos_days,
        "lookback_days": days, "versions": out, "adopted": adopted,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 볼트 영구 기록(로직 정의 + 백테스트 결과) + 디스코드 브리핑 — 스윙 하니스와 동일 패턴
    def _f(v, dp=2):
        return "—" if v is None else f"{v:+.{dp}f}%" if dp else f"{v}"
    md_lines = [f"# ⚡ 단타 백테스트 · {_DM.today_kst().isoformat()}",
                f"> 유니버스 {len(notes)}종목 · {days}일 · OOS {oos_start}~{oos_end} · 가상 {seed:,.0f}원", ""]
    brief_bits = []
    for v in out:
        o = v["oos"]
        md_lines += [f"## {v['label']} — {v['title']}", "**핵심 로직**"]
        md_lines += [f"- {c}" for c in v["core_logic"]]
        md_lines += ["", f"**OOS 성과**: 기대값 {_f(o['expectancy'])} · PF {o['profit_factor'] or '—'} · "
                     f"MDD {_f(o['max_drawdown'], 1)} · 승률 {o['win_rate'] or '—'}% · "
                     f"{o['n_trades']}거래 · 누적 {_f(o['cum_return_pct'], 1)}", ""]
        brief_bits.append(f"{v['label']} 기대값 {_f(o['expectancy'])}·PF {o['profit_factor'] or '—'}"
                          f"·승률 {o['win_rate'] or '—'}%({o['n_trades']}건)")
    vpath = VaultWriter(cfg).write_scalp_backtest("\n".join(md_lines))
    from .notify.discord import notify
    notify(cfg.creds.scalp_webhook,
           "⚡ **단타 백테스트 리플레이** — " + " / ".join(brief_bits) +
           f"\n볼트: {vpath.name} · 대시보드 '버전 비교 > 초단기 단타' 갱신됨")
    log.info("scalp_compare → %s (v1 %d·v2 %d OOS거래)", path,
             out[0]["oos"]["n_trades"], out[1]["oos"]["n_trades"])
    return path


# ── 단타(데이트레이딩) ──
def _scalp_bar(df, d: str):
    sub = df[df.index.normalize() == pd.Timestamp(d)]
    return None if sub.empty else sub.iloc[-1]


def _settle_scalp_plan(plan: dict, dfs: dict, fee_bps: float, slip_bps: float, models: tuple):
    """저장된 계획을 확정 일봉으로 정산 — 전량(all-or-nothing) 원칙.

    라이브 모델(models)의 계획 종목(그림자 포함) 중 단 하나라도 해당 날짜 확정봉이 없으면
    부분 정산으로 pnl 이 조용히 새지 않도록 (None, None, missing)=보류를 반환한다.
    옛 채택 모델의 잔여 계획 항목은 무시(라이브 모델만 정산)."""
    from .scalp.strategy import settle_item
    items = [i for i in plan.get("items", []) if i.model in models]
    if not items:
        return ({m: {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []} for m in models},
                {m: [] for m in models}, [])
    bars: dict = {}
    missing: list[str] = []
    for i in items:
        if i.ticker in bars:
            continue
        bar = _scalp_bar(dfs[i.ticker], plan["date"]) if i.ticker in dfs else None
        bars[i.ticker] = bar
        if bar is None:
            missing.append(i.ticker)
    if missing:
        return None, None, missing
    results = {m: {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []} for m in models}
    rows: dict = {m: [] for m in models}
    for i in items:
        bar = bars[i.ticker]
        f = settle_item(i, bar, fee_bps, slip_bps)
        if f is None:
            continue
        if i.shadow:
            results[i.model]["shadow_pnl"] += f.pnl
            continue
        results[i.model]["pnl"] += f.pnl
        row = {"ticker": i.ticker, "name": i.name, "qty": i.qty, "entry": round(f.entry, 2),
               "exit": round(f.exit, 2), "pnl": round(f.pnl, 0), "ret_pct": f.ret_pct,
               "reason": f.reason, "why": i.why}
        results[i.model]["trades"].append(row)
        rows[i.model].append(row)
    return results, rows, []


def run_scalp(cfg: Config, market: str) -> dict:
    """단타 페이퍼 1사이클: 이전 계획 정산 → 오늘 계획 수립 → 발송/저장/마커."""
    from .market.realtime import get_quote
    from .notify import health as _H
    from .notify.discord import notify_embeds
    from .scalp import planner as _P
    from .scalp.account import ScalpState, live_models
    from .scalp.briefer import scalp_brief
    from .scalp.strategy import v3_setup_ok
    reader = VaultReader(cfg)
    provider = _provider(cfg)
    notes = [n for n in _load_notes(cfg, reader, None, market) if n.ticker]
    fee = float(cfg.get("paper", "fee_bps", default=1.5))
    slip = float(cfg.get("paper", "slippage_bps", default=5.0))
    today = _DM.today_kst().isoformat()
    models = live_models(cfg)          # 라이브 = 채택 모델 1개(비교는 scalp_compare 백테스트)
    if "v5" in models or "v6" in models:
        # v5/v6 오버나잇 상따는 15시 장중 스캔 런(scalp-v6)이 정산·계획·브리핑을 전담 —
        # 저녁 v1~v4 사이클(전일계획→당일 인트라데이 정산)과 구조가 달라 여기선 생략.
        log.info("scalp-run[%s]: 채택 %s — 15시 스캔 런(scalp-v6) 전담, 저녁 사이클 생략", market, models[0] if models else "?")
        return {"settled": 0, "planned": 0, "warned": False, "held": False}
    state = ScalpState.load(cfg.state_dir, models)
    warned = False

    # 후보 지표(전일 확정봉) — 유동성 하한: scalp.min_tv_eok(기본 50억)
    min_tv = float(cfg.get("scalp", "min_tv_eok", default=50))
    cands, dfs = [], {}
    for n in notes:
        try:
            df, _src = provider.get_ohlcv(n.ticker)
        except Exception as e:  # noqa: BLE001 — 종목 하나 실패가 전체를 못 막게
            log.warning("scalp 후보 시세 실패 %s: %s", n.ticker, e)
            continue
        if df is None or len(df) < 61:
            continue
        dfs[n.ticker] = df
        prev = df.iloc[-1]
        tv_eok = float(prev["close"]) * float(prev.get("volume", 0)) / 1e8
        if tv_eok < min_tv:
            continue
        ma20 = float(df["close"].tail(20).mean())
        ma60 = float(df["close"].tail(60).mean())
        from .scalp.strategy import V4_SURGE
        v4_ok = (len(df) >= 2 and float(df["close"].iloc[-2]) > 0
                 and float(df["close"].iloc[-1]) >= float(df["close"].iloc[-2]) * (1 + V4_SURGE / 100))
        cands.append({"ticker": n.ticker, "name": n.name or n.ticker,
                      "prev_close": float(prev["close"]),
                      "prev_range": float(prev["high"]) - float(prev["low"]),
                      "prev_tv_eok": round(tv_eok, 1), "uptrend": ma20 > ma60,
                      "v3_ok": v3_setup_ok(df["close"], df["volume"] if "volume" in df else None),
                      "v4_ok": v4_ok,   # 전일 급등(+5%↑) → 급등 후 첫 조정(눌림목) 반등 대상
                      "why": f"거래대금 {tv_eok:,.0f}억"})

    # 1) 이전 계획 정산(확정 일봉이 정본) — 전량(all-or-nothing): 하나라도 미확보면 보류(재시도)
    plans = _P.load_plans(cfg.state_dir)
    prev_plan = plans.get(market)
    settled_rows = {m: [] for m in models}
    settled_date = "—"
    n_settled = 0
    held = False
    if prev_plan and prev_plan["date"] < today:
        # 오늘 후보 유니버스에 없던(노트 이탈 등) 이전 계획 종목도 정산 대상이면 명시적으로 시세 보강
        for i in prev_plan.get("items", []):
            if i.ticker in dfs:
                continue
            try:
                df, _src = provider.get_ohlcv(i.ticker)
            except Exception as e:  # noqa: BLE001 — 개별 실패는 로그 후 missing 판정에 맡김
                log.warning("scalp 정산용 시세 실패 %s: %s", i.ticker, e)
                continue
            if df is not None:
                dfs[i.ticker] = df
        results, rows, missing = _settle_scalp_plan(prev_plan, dfs, fee, slip, models)
        if missing:
            warned = True
            expired = (date.fromisoformat(today) - date.fromisoformat(prev_plan["date"])).days >= 5
            if expired:
                zero = {m: {"pnl": 0.0, "shadow_pnl": 0.0, "trades": []} for m in models}
                state.apply_day(prev_plan["date"], market, zero)
                state.save(cfg.state_dir)
                _H.alert(cfg.creds.scalp_webhook, f"단타 정산[{market}]",
                         f"단타 정산 만료 — {prev_plan['date']} 데이터 미확보로 결과 미산정(0 처리)")
            else:
                held = True
                _H.alert(cfg.creds.scalp_webhook, f"단타 정산[{market}]",
                         f"{prev_plan['date']} 확정 일봉 미도착({', '.join(missing)}) — "
                         "정산 보류(다음 런 재시도)")
        else:
            state.apply_day(prev_plan["date"], market, results)
            state.save(cfg.state_dir)
            settled_rows, settled_date = rows, prev_plan["date"]
            n_settled = sum(len(v) for v in rows.values())

    if held:
        # 정산 미확보 — 오늘 계획을 만들지 않고 저장된 계획을 보존(단일 슬롯 store 덮어쓰기 방지),
        # Daily 브리핑도 생략. 다음 런이 같은 계획을 다시 정산 시도한다.
        return {"settled": 0, "planned": 0, "warned": True, "held": True}

    state.save(cfg.state_dir)   # 첫 런(정산 없음)에도 시드 상태를 기록 — 대시보드 /api/scalp 가동 표시

    # 2) 오늘 계획(실시간가로 수량/트리거 표시 — 정산은 어차피 확정봉)
    from .market.fx import get_usdkrw
    fx = get_usdkrw(float(cfg.get("market_data", "fx_usdkrw", default=1400)))
    scenario = _P.build_scenario(cfg, reader)
    quote_objs: dict = {}
    for c in cands[:20]:
        q = get_quote(c["ticker"], fx)
        if q:
            quote_objs[c["ticker"]] = q
    quotes = {t: q.price for t, q in quote_objs.items()}
    if cands and not quotes:
        warned = True
        _H.alert(cfg.creds.scalp_webhook, f"단타 계획[{market}]",
                 "실시간 시세 전부 실패 — 전일 종가로 수량 산정(트리거 표시 생략)")
    cash_by = {m: state.models[m]["cash"] for m in models}
    plan_lists = _P.build_plan(cands, cash_by, scenario, quotes, models)
    items = [it for m in models for it in (plan_lists[m] + plan_lists[f"{m}_shadow"])]
    # KR 표시용 트리거(v1) = 실시간 시가 + k×전일레인지
    from dataclasses import replace
    disp = []
    for i in items:
        qq = quote_objs.get(i.ticker)
        # US는 계획 시점에 대상 세션 시가 미확보(직전 세션 값) → 오도 방지 위해 트리거 표시 생략(정산은 확정봉 기준)
        if i.model == "v1" and market == "kr" and qq and not i.shadow and qq.open:
            i = replace(i, trigger=round(qq.open + (i.k or 0.5) * i.prev_range, 0))
        disp.append(i)
    plan = {"date": today, "scenario": scenario, "items": disp}
    _P.save_plan(cfg.state_dir, market, plan)

    # 3) 브리핑(디스코드+볼트) + 마커
    embed, md = scalp_brief(market, settled_rows, plan, state, settled_date)
    notify_embeds(cfg.creds.scalp_webhook, [embed], md)
    VaultWriter(cfg).append_scalp(md)
    _DM.record_done(cfg.state_dir, f"scalp_{market}", datetime.now(_DM.KST))
    n_planned = sum(1 for i in disp if not i.shadow)
    log.info("scalp-run[%s]: 정산 %d건 · 계획 %d건", market, n_settled, n_planned)
    return {"settled": n_settled, "planned": n_planned, "warned": warned, "held": False}


__all__ = ["load_config", "run_scan", "run_once", "run_review", "run_backtest", "run_doctor",
           "run_brief", "run_logic", "run_logic_review", "_setup_logging", "run_harness",
           "run_version_compare", "run_scalp", "run_scalp_compare"]
