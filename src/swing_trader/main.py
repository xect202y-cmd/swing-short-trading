"""파이프라인 — scan / run-once / review / backtest / doctor.

각 단계는 '왜 매수/매도/차단했는지'를 마크다운에 남긴다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

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
        reader.event_calendar(), date.today(),
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
    payload = {"date": date.today().isoformat(), "market": market,
               "candidates": [rec(s) for s in sorted(cands, key=lambda x: x.rank or 999)]}
    ddir = cfg.state_dir / "decision_log"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / f"{date.today().isoformat()}_{market}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_run(cfg: Config, signals: list[Signal], orders: list[Order]) -> None:
    def ser(o):
        d = asdict(o) if is_dataclass(o) else dict(o)
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        d["stop"] = getattr(o, "stop", None)
        d["target"] = getattr(o, "target", None)
        return d
    payload = {
        "date": date.today().isoformat(),
        "orders": [ser(o) for o in orders],
        "signals": [{"ticker": s.ticker, "name": s.name, "kind": s.kind.value,
                     "score": s.score, "event_risk": s.event_risk.value} for s in signals],
    }
    (cfg.state_dir / "last_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
    advanced = broker.advance_bar(date.today().isoformat())
    # 1) 보유 청산 점검 먼저
    pm = PositionManager(cfg, broker, provider)
    exits = pm.check_and_exit()
    # 2) 신규 매수(안전장치)
    om = OrderManager(cfg, broker, realized_today=broker.realized_pnl, realized_total=broker.realized_pnl)
    result = om.execute_signals(signals)
    broker.save()

    # 청산 거래 원장 적재(분석/브리핑용)
    from .notify.discord import notify_embeds
    from .review import analytics as _A
    from .review import briefer as _B
    closed = [c for _, _, c in exits]
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

    # Daily 브리핑(성과+보유복기 표) → 디스코드 + 볼트 기록.
    # 시장 분리 실행 시 마지막(아침 한국) 런에서만 1회 발송(do_brief=False면 건너뜀).
    if do_brief:
        activity = {"bought": len(result.placed), "sold": len(exits), "blocked": result.blocked}
        daily_embed, daily_md = _B.daily_brief(cfg, broker, provider, signals, activity)
        notify_embeds(wh, [daily_embed], daily_md)
        writer.write_daily(daily_md)

    all_orders = [o for o, _, _ in exits] + result.placed
    # 빈 날에도 Trade.md 를 남겨 '왜 매매하지 않았는지'를 기록(조용히 넘기지 않는다).
    trade_path = writer.append_trades(all_orders)
    if not all_orders:
        n_buy = sum(s.kind.value == "BUY" for s in signals)
        with trade_path.open("a", encoding="utf-8") as f:
            f.write(f"\n> 오늘 신규 체결 없음 — 매수신호 {n_buy}건 · 차단 {len(result.blocked)}건. "
                    "보수적 회피(나쁜 타점엔 매매하지 않음). 상세 사유는 Signals.md 참조.\n")
    _save_run(cfg, signals, all_orders)

    log.info("run-once: 매수 %d · 매도 %d · 차단 %d", len(result.placed), len(exits), len(result.blocked))
    # 페일오버 마커: 이 시장 런이 정상 완료됨을 기록(클라우드가 읽어 중복 방지)
    from .state import daily_marker as _DM
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
    today = _date.today()
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
    path = writer.write_backtest(_BT.render_md(rows, days, take, stop, date.today().isoformat()))
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
    is_rep, oos_rep = _HN.report_from_trades(is_t), _HN.report_from_trades(oos_t)
    writer = VaultWriter(cfg)
    ver = _HN.logic_version_id(cfg)   # 결과를 현재 로직 버전으로 태깅(나중 버전별 A/B 키) → 옵시디언에 무조건 기록
    md = _HN.render_report_md("기준 로직 성과 측정", is_rep, oos_rep, date.today().isoformat(), version=ver)
    path = writer.write_harness(md)
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


__all__ = ["load_config", "run_scan", "run_once", "run_review", "run_backtest", "run_doctor",
           "run_brief", "run_logic", "run_logic_review", "_setup_logging", "run_harness"]
