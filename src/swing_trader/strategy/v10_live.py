"""v10 라이브 — 오늘 거감짜름 진입 신호 빌더 + 라이브 사이클.

run_once 의 KR 사이클을 본떠, 진입 신호 소스만 SignalEngine(노트) → v10 전시장 스캔으로 교체.
청산/사이징/영속화는 기존 PositionManager/OrderManager/analytics/briefer 재사용(Option B).
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta

from ..market.supply import supply_ok
from ..models import Signal, SignalKind
from . import risk as risk_mod
from .v10_new_high import _params_from_cfg, regime_ok, scan_candidates


def build_v10_signals(cfg, panel: dict, d: str, supply, kospi_up, kosdaq_up,
                      market_of: dict) -> list[Signal]:
    """오늘(d) 거감짜름 진입 후보 중 라이브 게이트 통과분을 매수 Signal 로.

    수급: 라이브 페일오픈(None=데이터없음 → 진입 허용, False=순매도 확정 → 차단).
    시황: regime_ok(up 집합 None → 페일오픈). 룩어헤드 없음(entry_date==d 후보만, ≤d 데이터).
    """
    p = _params_from_cfg(cfg)
    stop_pct = float(cfg.get("risk", "default_stop_pct", default=-3.0))
    take1_pct = float(cfg.get("risk", "take1_pct", default=6.0))
    max_stop_pct = float(cfg.get("risk", "max_stop_pct", default=-5.0))
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
            if supply_ok(netbuy, d, p["supply_days"]) is False:   # 명시적 순매도만 차단(None=페일오픈)
                continue
            plan = risk_mod.build_plan(c.entry_price, default_stop_pct=stop_pct, take1_pct=take1_pct,
                                       max_stop_pct=max_stop_pct)
            score = 80.0 + (5.0 if c.all_time else 0.0) + (3.0 if c.hist_vol else 0.0)
            out.append(Signal(
                ticker=ticker, name=ticker, kind=SignalKind.BUY, score=score,
                price=c.entry_price, plan=plan, sector=None,
                reasons=[f"v10 거감짜름 진입(d={d})",
                         *(["역사적 신고가"] if c.all_time else []),
                         *(["역사적 거래량"] if c.hist_vol else [])],
            ))
    return out


class _PanelProvider:
    """provider.get_ohlcv 인터페이스 어댑터 — 이미 메모리의 전시장 패널로 응답(네트워크 없음).

    v10 은 매 사이클 krx_panel.pkl 전체를 로드하므로, 보유종목 현재가/지표 조회도 같은
    스냅샷(d)에서 얻는 게 일관적이다(종목별 개별 재조회 시 provider 소스가 갈릴 위험 방지).
    """

    def __init__(self, panel: dict):
        self.panel = panel
        self.sources = {t: ("panel" if v is not None else "missing") for t, v in panel.items()}

    def get_ohlcv(self, ticker: str):
        return self.panel.get(ticker), "panel"


def _load_panel(cfg):
    """(panel, market_of, d) — krx_panel.pkl 로드. 없으면 RuntimeError(synthetic 성과 금지)."""
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
    VaultWriter(cfg).append_swing_v10(md, _date.fromisoformat(d))


def run_v10_live(cfg) -> dict:
    """v10 KR 스윙 라이브 1사이클 — 브로커 인수·v7 청산·v10 진입·3면 브리핑(멱등).

    영속화는 전부 단일 브로커에서 파생(analytics/briefer 공용 함수만 사용) — split-brain 방지.
    """
    from ..broker.paper import PaperBroker
    from ..execution.order_manager import OrderManager
    from ..execution.position_manager import PositionManager
    from ..review import analytics as _A
    from ..review import briefer as _B
    from ..state import daily_marker as _DM

    panel, market_of, d = _load_panel(cfg)
    provider = _PanelProvider(panel)
    seed = float(cfg.get("capital", "seed", default=5_000_000))
    broker = PaperBroker(
        seed_cash=seed, state_path=cfg.state_dir / "paper_state.json",
        price_fn=lambda t: (panel[t]["close"].iloc[-1] if panel.get(t) is not None else None),
        fee_bps=float(cfg.get("paper", "fee_bps", default=1.5)),
        slippage_bps=float(cfg.get("paper", "slippage_bps", default=5.0)),
    )
    fresh = broker.advance_bar(d)          # 하루 1회 bars_held++ (멱등 게이트)
    exited = entered = 0
    closed_recs: list[dict] = []
    placed: list = []
    if fresh:
        pm = PositionManager(cfg, broker, provider)
        for _order, _reasons, closed in pm.check_and_exit():   # v7 청산(인수 보유 포함) — 로직 불변
            closed_recs.append(closed)
            exited += 1
        ma = int(cfg.get("v10", "regime_ma", default=50))
        kospi_up, kosdaq_up = _regime_updays(cfg, ma)
        supply = _supply_provider(cfg)
        signals = build_v10_signals(cfg, panel, d, supply, kospi_up, kosdaq_up, market_of)
        for sig in signals:
            # v10 신호엔 실제 섹터 데이터가 없다(FDR KRX 상장목록에 Sector/Industry 컬럼 없음 —
            # 확인 후 근사치 fudge 대신 종목코드=섹터로 독립 버킷화). sector=None 이면 전부 '기타'
            # 버킷에 몰려 max_sector_pct(35%) 가 2번째 진입(40%)부터 오탐 차단 — 3슬롯 설계 무력화.
            sig.sector = sig.ticker
        today = _date.fromisoformat(d)
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        realized_today = _A.realized_since(cfg.state_dir, d, closed_recs)
        realized_week = _A.realized_since(cfg.state_dir, week_start, closed_recs)
        om = OrderManager(cfg, broker, realized_today=realized_today, realized_total=realized_week)
        res = om.execute_signals(signals)   # 사이징/게이팅 재사용
        placed = res.placed
        entered = len(placed)
        broker.save()
        if closed_recs:
            _A.record_closed_trades(cfg.state_dir, closed_recs)

    # 영속화 — 대시보드 파일 전부 단일 브로커에서 파생(청산/진입 유무와 무관하게 매 사이클 갱신)
    pos, hv = _B._positions_data(cfg, broker, provider)          # open_positions.json
    _A.record_equity(cfg.state_dir, d, broker.get_cash_balance(), hv, seed)  # equity_history.json

    # 브리핑(디스코드 + 옵시디언)
    op_lines = [f"  · {p['name']}({p['ticker']}) {p['qty']}주 · {p['ret']:+.1f}% · {p['days']}일"
                for p in pos[:15]] or ["  · 없음"]
    ex_lines = [f"  · {c['ticker']} {(c.get('return_pct') or 0):+.1f}% "
                f"({round(c.get('pnl', 0) or 0):,}원 · {c.get('exit_reason', '')})"
                for c in closed_recs] or ["  · 없음"]
    en_lines = [f"  · {o.ticker} {o.quantity}주 @ {round(o.filled_price or o.price):,}원"
                for o in placed] or ["  · 없음"]
    fields = [
        {"name": f"📤 청산 {exited}건", "value": "\n".join(ex_lines)[:1024], "inline": False},
        {"name": f"📥 신규 진입 {entered}건", "value": "\n".join(en_lines)[:1024], "inline": False},
        {"name": f"📊 보유 {len(pos)}종목", "value": "\n".join(op_lines)[:1024], "inline": False},
    ]
    embed = {"title": f"🆕 스윙 V10 · {d}", "color": 0xE74C3C, "fields": fields,
             "footer": {"text": f"신고가 거감짜름(보컬 김영준) · KR 코스피+코스닥 · "
                                f"실현 {round(broker.realized_pnl):,}원"}}
    md = (f"### 🆕 스윙 V10 · {d}\n> 신고가 거감짜름(보컬 김영준) · 청산 v7(추세추종) · 진입 거감짜름\n"
          f"**청산 {exited} · 진입 {entered} · 보유 {len(pos)}**\n" + "\n".join(op_lines) + "\n")
    _notify(cfg, embed, md)
    _write_vault(cfg, md, d)
    _DM.record_done(cfg.state_dir, "kr", datetime.now(_DM.KST))   # v10=KR 스윙 채택모델 → 'kr' 마커(클라우드 페일오버 정합)
    return {"exited": exited, "entered": entered, "held": len(pos),
            "realized": round(broker.realized_pnl), "asOf": d}
