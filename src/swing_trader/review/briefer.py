"""브리핑 빌더 — 매수/매도 알림 · Daily · Weekly · Monthly(로직 제안).

각 함수는 (embed, markdown) 를 만든다.
  - embed: Discord 임베드(카드) — 모바일 가시성·표 형태·KR🇰🇷/US🇺🇸 구분.
  - markdown: 옵시디언 기록용(실제 표) + 웹훅 없을 때 콘솔 폴백.
종목 복기는 룰 기반(현재가 vs 매수가/목표/손절, 보유일, 점수-결과).
"""
from __future__ import annotations

from ..config import Config
from ..market.data_provider import DataProvider
from ..market.technicals import build_snapshot
from ..models import Order, Position, Signal, SignalKind, now_kst
from ..state.daily_marker import today_kst
from . import analytics as A

GREEN, RED, BLUE, PURPLE = 0x2ECC71, 0xE74C3C, 0x3498DB, 0x9B59B6


def _is_kr(ticker: str | None) -> bool:
    c = (ticker or "").split(".")[0]
    return c.isdigit() and len(c) == 6


def _flag(ticker: str | None) -> str:
    return "🇰🇷" if _is_kr(ticker) else "🇺🇸"


def _mkt(ticker: str | None) -> str:
    return "KR" if _is_kr(ticker) else "US"


def _won(v) -> str:
    return "—" if v is None else f"{round(v):,}"


def _pxd(ticker: str | None, krw, fx: float | None = None) -> str:
    """가격 표시 — 미국주는 '$원가(₩환산)' 병기, 한국주는 원화. fx 없으면 실시간 환율(캐시)."""
    if krw is None:
        return "—"
    if _is_kr(ticker):
        return f"{round(krw):,}원"
    if fx is None:
        from ..market.fx import get_usdkrw
        fx = get_usdkrw()
    return f"${krw / fx:,.2f}(₩{round(krw):,})"


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _breakdown_inline(sig) -> str:
    if not sig.breakdown or not sig.breakdown.items:
        return ""
    parts = [f"{i.name.split('/')[0].strip()} {i.score:.0f}/{i.max_score:.0f}" for i in sig.breakdown.items]
    return " · ".join(parts)


def _rationale_lines(sig) -> list[str]:
    r = sig.rationale or {}
    if not r:
        return []
    return [
        f"[진입근거] 유형: {r.get('entry_type','-')}",
        f"  가격: {', '.join(r.get('price_conditions', []))} · 거래량: {r.get('volume_condition','-')}",
        f"  추세: {r.get('trend_condition','-')} · 섹터: {r.get('sector_state','-')}",
        f"  시장: {r.get('market_state','-')}",
        f"  제외조건: {r.get('exclusion_pass','-')}",
    ]


def _target_lines(sig) -> list[str]:
    tm = sig.target_methods or {}
    used = sig.target_method_used or "-"
    out = [f"[목표/손절] 방식: {used}"]
    for name, m in tm.items():
        mark = "▶" if name == used else " "
        out.append(f"  {mark} {name}: 목표 {m['target']:,.0f}/손절 {m['stop']:,.0f} (손익비 {m.get('rr')}, {m.get('note','')})")
    return out


def _md_table(headers: list[str], rows: list[list]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
    return f"{head}\n{sep}\n{body}" if rows else f"{head}\n{sep}\n| (없음) |"


# ── 매수/매도 즉시 알림 ──
def trade_buy_alert(orders: list[Order], cash: float, n_positions: int,
                    sig_map: dict, fx: float = 1400.0) -> tuple[dict | None, str]:
    buys = [o for o in orders if o.side == "BUY" and o.status == "filled"]
    if not buys:
        return None, ""
    fields, rows = [], []
    for o in buys:
        sig = sig_map.get(o.ticker)
        nm = sig.name if sig else o.ticker
        px = o.filled_price or o.price
        stop, tgt = getattr(o, "stop", None), getattr(o, "target", None)
        rr = None
        if stop and tgt and px:
            risk = abs((stop - px) / px)
            rr = round(((tgt - px) / px) / risk, 2) if risk else None
        budget = px * o.quantity
        score = (o.note or "").replace("점수 ", "").split(" ")[0] if "점수" in (o.note or "") else "—"
        method = sig.target_method_used if sig else "-"
        rank = f"(#{sig.rank})" if sig and sig.rank else ""
        head = (f"매수 **{_pxd(o.ticker, px, fx)}** → 목표 {_pxd(o.ticker, tgt, fx)} / 손절 {_pxd(o.ticker, stop, fx)} (방식 {method})\n"
                f"손익비 {rr or '—'} · 점수 {score}{rank} · 투입 {_won(budget)}원")
        extra = ""
        if sig and sig.methods:
            extra += f"\n🎯 기법: {', '.join(sig.methods)}"
        if sig and sig.ai_verdict:
            extra += f"\n🤖 AI {sig.ai_verdict}({sig.ai_confidence}): {sig.ai_reason}"
        bd = _breakdown_inline(sig) if sig else ""
        rtl = "\n".join(_rationale_lines(sig)) if sig else ""
        value = head + extra + (f"\n[점수분해] {bd}" if bd else "") + (f"\n{rtl}" if rtl else "")
        fields.append({"name": f"{_flag(o.ticker)} {nm} ({o.ticker}) · {o.quantity}주",
                       "value": value[:1024], "inline": False})
        # 옵시디언 md — 분석용 풀 디테일(진입근거 + 점수분해 + 목표/손절 3방식 비교)
        rows.append(f"#### {_flag(o.ticker)} {nm} ({o.ticker}) · {o.quantity}주 @ {_won(px)}")
        rows.append(head.replace("**", ""))
        if sig:
            rows += _rationale_lines(sig)
            if sig.breakdown:
                rows.append("[점수분해] " + " · ".join(sig.breakdown.as_lines()).replace("- ", ""))
            rows += _target_lines(sig)
    embed = {"title": "🟢 스윙 매수 체결", "color": GREEN, "fields": fields,
             "footer": {"text": f"가용현금 {_won(cash)}원 · 보유 {n_positions}종목"}}
    md = "### 🟢 매수 체결\n" + "\n".join(rows)
    return embed, md


def trade_sell_alert(closed: list[dict]) -> tuple[dict | None, str]:
    if not closed:
        return None, ""
    fields, rows = [], []
    for t in closed:
        win = t.get("pnl", 0) > 0
        tk = t.get("ticker", "")
        fields.append({
            "name": f"{'✅' if win else '❌'} {_flag(tk)} {t['name']} · {_pct(t.get('return_pct'))}",
            "value": f"매도 **{_won(t.get('exit'))}** · 실현손익 {_won(t.get('pnl'))}원\n사유: {t.get('exit_reason', '—')}",
            "inline": False,
        })
        rows.append([("WIN" if win else "LOSS"), _flag(tk), t["name"], _won(t.get("exit")),
                     _pct(t.get("return_pct")), _won(t.get("pnl")), t.get("exit_reason", "—")])
    embed = {"title": "🔴 스윙 매도/청산", "color": RED, "fields": fields}
    md = "### 🔴 매도/청산\n" + _md_table(["결과", "장", "종목", "매도가", "수익율", "실현손익", "사유"], rows)
    return embed, md


# ── 보유 종목 현황 + 복기 ──
def _review_position(pos: Position, cur: float, tech) -> str:
    ret = pos.unrealized_pct(cur)
    to_tgt = (pos.target1 - cur) / cur * 100 if pos.target1 else None
    to_stop = (pos.stop - cur) / cur * 100 if pos.stop else None
    notes = []
    if ret >= 3:
        notes.append("수익 구간 — 1차 익절 관찰")
    elif ret <= -2:
        notes.append("손실 구간 — 손절 주의")
    if to_tgt is not None and to_tgt <= 1.5:
        notes.append("목표가 근접")
    if to_stop is not None and to_stop >= -1.5:
        notes.append("손절가 근접(경계)")
    if tech and tech.rsi and tech.rsi >= 70:
        notes.append(f"RSI {tech.rsi} 과열")
    if pos.bars_held >= 5:
        notes.append(f"보유 {pos.bars_held}일(장기화)")
    if pos.entry_score is not None:
        notes.append(f"진입 {pos.entry_score:.0f}점 → {'좋은 타점' if ret >= 0 else '재점검'}")
    return "; ".join(notes) or "계획대로 보유 중"


def _positions_data(cfg: Config, broker, provider: DataProvider):
    """보유 종목 dict 리스트 + 평가금. dict: flag/mkt/name/ticker/avg/cur/ret/pnl/qty/days/stop/target/review/fx.
    겸사겸사 state/open_positions.json 스냅샷도 저장 → 헤르메스 대시보드 성과탭이 페이퍼 보유종목·수익률을 읽음."""
    out, holdings_value = [], 0.0
    ind = cfg.get("indicators", default={})
    from ..market.fx import get_usdkrw
    fx = get_usdkrw(float(cfg.get("market_data", "fx_usdkrw", default=1400)))
    for p in broker.get_positions():
        df, _ = provider.get_ohlcv(p.ticker)
        cur = float(df["close"].iloc[-1])
        tech = build_snapshot(p.ticker, df, ind)
        holdings_value += cur * p.quantity
        out.append({
            "flag": _flag(p.ticker), "mkt": _mkt(p.ticker), "name": p.name, "ticker": p.ticker,
            "avg": p.avg_price, "cur": cur, "ret": p.unrealized_pct(cur), "pnl": p.unrealized_pnl(cur),
            "qty": p.quantity, "days": p.bars_held, "stop": p.stop, "target": p.target1,
            "entry_date": p.entry_date, "entry_score": p.entry_score,
            "review": _review_position(p, cur, tech), "fx": fx,
        })
    _save_open_positions(cfg, broker, out, holdings_value)
    return out, holdings_value


def _save_open_positions(cfg: Config, broker, positions: list[dict], holdings_value: float) -> None:
    """대시보드용 보유종목 스냅샷. 현재가/평가손익이 담긴 유일한 영속 파일(paper_state.json엔 평단가만 있음)."""
    import json
    snapshot = {
        "asOf": today_kst().isoformat(),
        "cash": round(broker.get_cash_balance(), 0),
        "holdingsValue": round(holdings_value, 0),
        "positions": positions,
    }
    try:
        (cfg.state_dir / "open_positions.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # 스냅샷 저장 실패가 브리핑을 막지 않도록


def _holdings_fields(pos: list[dict]) -> list[dict]:
    if not pos:
        return [{"name": "💼 보유 종목", "value": "보유 없음", "inline": False}]
    return [{
        "name": f"{p['flag']} {p['name']} ({p['ticker']}) · {_pct(p['ret'])}",
        "value": f"매수 {_pxd(p['ticker'], p['avg'], p['fx'])} → 현재 **{_pxd(p['ticker'], p['cur'], p['fx'])}** · 보유 {p['days']}일\n"
                 f"목표 {_pxd(p['ticker'], p['target'], p['fx'])} / 손절 {_pxd(p['ticker'], p['stop'], p['fx'])}\n복기: {p['review']}",
        "inline": False,
    } for p in pos]


def _holdings_md(pos: list[dict]) -> str:
    rows = [[p["flag"], p["name"], _pxd(p["ticker"], p["avg"], p["fx"]), _pxd(p["ticker"], p["cur"], p["fx"]),
             _pct(p["ret"]), f"{p['days']}일", p["review"]] for p in pos]
    return _md_table(["장", "종목", "매수가", "현재가", "수익율", "보유", "복기"], rows)


def _metrics_lines(m: A.Metrics) -> list[str]:
    out = [
        f"누적수익율 **{_pct(m.return_pct)}** · 실현 {_won(m.total_realized)}원",
        f"청산 {m.n_closed}건 · 승률 {m.win_rate if m.win_rate is not None else '—'}% · PF {m.profit_factor or '—'}",
        f"평균수익 {_pct(m.avg_win_pct)} · 평균손실 {_pct(m.avg_loss_pct)} · MDD {_pct(m.max_drawdown_pct)}",
        f"목표달성 {m.target_hit} · 손절 {m.stop_hit} · 손절준수 {m.rule_adherence if m.rule_adherence is not None else '—'}%",
    ]
    if m.by_score_band:
        out.append("점수대별 승률 → " + " · ".join(f"{k}:{v['win_rate']:.0f}%({v['n']})" for k, v in m.by_score_band.items()))
    return out


def _seed(cfg: Config) -> float:
    return float(cfg.get("capital", "seed", default=1_000_000))


def _compound_line(cfg: Config) -> str:
    """복리 연환산 추정(목표=복리로 1년 얼마)."""
    proj = A.compound_projection(A.load_equity(cfg.state_dir), _seed(cfg))
    if not proj:
        return ""
    if proj.get("annual_pct") is None:
        return f"📈 복리 추정: 표본 {proj['days']}일(누적 {proj['total_pct']:+.1f}%) — 더 축적 필요"
    return (f"📈 복리 환산(현재 페이스·{proj['days']}일·추정): 주 {proj['weekly_pct']:+.1f}% · "
            f"월 {proj['monthly_pct']:+.1f}% · 연 {proj['annual_pct']:+.0f}% → 1년후 ~{proj['annual_won']:,.0f}원")


def _portfolio_lines(cfg: Config, broker, provider) -> list[str]:
    from ..strategy import portfolio as pf
    seed = _seed(cfg)
    exp = pf.Exposure(seed, broker.get_positions(),
                      lambda t: provider.get_ohlcv(t)[0]["close"].iloc[-1] if t else None)
    lim = pf.limits_from_cfg(cfg)
    rep = exp.report(lim)
    over = "초과 ⚠️" if rep["total_pct"] > rep["max_total"] else "여유 ✅"
    secs = ", ".join(f"{s} {p}%" for s, p in rep["per_sector_pct"].items()) or "없음"
    return [
        f"총 투자비중 {rep['total_pct']}% / 한도 {rep['max_total']}% ({over})",
        f"섹터 비중: {secs} (한도 {rep['max_sector']}%)",
        f"종목 한도 {rep['max_per_stock']}% · 하루 신규 한도 {lim['max_new']}종목",
    ]


def _ranking_lines(signals: list[Signal], top: int = 7) -> list[str]:
    ranked = sorted([s for s in signals if s.rank], key=lambda s: s.rank)[:top]
    tagmap = {"BUY": "매수", "BUY_WATCH": "WATCH", "BLOCKED": "차단", "HOLD": "보류"}
    out = []
    for s in ranked:
        tag = tagmap.get(s.kind.value, s.kind.value)
        why = f" — {s.blocked_reasons[0]}" if s.kind == SignalKind.BLOCKED and s.blocked_reasons else ""
        out.append(f"{s.rank}. {_flag(s.ticker)}{s.name} {s.score:.0f}점 → {tag}{why}")
    return out


def _blocked_lines(blocked: list) -> list[str]:
    out = []
    for sig, reasons in blocked[:5]:
        rr = sig.plan.reward_risk if sig.plan else None
        out.append(f"• {_flag(sig.ticker)}{sig.name} {sig.score:.0f}점 · 손익비 {rr or '—'} · 순위 {sig.rank or '-'}")
        out.append(f"   사유: {reasons[0] if reasons else '-'}")
        if sig.plan:
            out.append(f"   예상 진입 {_pxd(sig.ticker, sig.plan.entry)}/목표 {_pxd(sig.ticker, sig.plan.target1)}/손절 {_pxd(sig.ticker, sig.plan.stop)} · 추적ON")
    return out


def _watch_lines(signals: list[Signal], top: int = 5) -> list[str]:
    watch = sorted([s for s in signals if s.kind == SignalKind.BUY_WATCH], key=lambda s: -s.score)
    out = []
    for s in watch[:top]:
        why = (s.blocked_reasons[0] if s.blocked_reasons else
               ("점수 높지만 매수조건 미충족(진입가 대기)" if s.score >= 70 else "관찰 구간(손익비/추세 확인)"))
        out.append(f"• {_flag(s.ticker)}{s.name} {s.score:.0f}점 — {why}")
        if s.rationale:
            out.append(f"   조건: {', '.join(s.rationale.get('price_conditions', []))} · 거래량 {s.rationale.get('volume_condition','-')}")
        if s.plan:
            out.append(f"   예상 진입 {_pxd(s.ticker, s.plan.entry)}/목표 {_pxd(s.ticker, s.plan.target1)}/손절 {_pxd(s.ticker, s.plan.stop)}")
    return out


def _realism_lines(cfg: Config) -> list[str]:
    paper = cfg.get("paper", default={})
    mtv = cfg.get("risk", "min_trading_value_eok", default=30)
    return [
        f"수수료 {paper.get('fee_bps',1.5)}bps 반영 · 슬리피지 {paper.get('slippage_bps',5)}bps 반영",
        f"체결가 기준: {paper.get('fill_mode','next_open')} (전일 확정캔들 판단→당일 시가 진입, look-ahead 방지)",
        f"거래량 필터: 거래대금 {mtv}억 미만 차단 · 갭/상하한 next_open 반영",
    ]


def _us_sleeve_krw(state_dir) -> float:
    """V1 US 슬리브 보유 마크가치(원) — 현금 공유풀 설계상 스윙 자산곡선/보유평가에 합산."""
    import json
    from pathlib import Path
    try:
        d = json.loads((Path(state_dir) / "v1_us_state.json").read_text(encoding="utf-8"))
        return float(sum((p.get("qty") or 0) * (p.get("cur_usd") or p.get("entry_usd") or 0)
                         * (p.get("mark_fx") or p.get("fx_entry") or 0) for p in d.get("open", [])))
    except Exception:  # noqa: BLE001
        return 0.0


def holdings_value_krw(state_dir, hv: float) -> float:
    """스윙 자산곡선/보유평가 표준 보유가치(원) = KR 마크(hv) + US 슬리브. 단일 원천 —
    daily_brief·v10_live 둘 다 이걸 호출해야 공식 비대칭(US 슬리브 누락)이 재발하지 않는다."""
    return hv + _us_sleeve_krw(state_dir)


# ── Daily ──
def daily_brief(cfg: Config, broker, provider, signals: list[Signal], activity: dict) -> tuple[dict, str]:
    d = today_kst().isoformat()
    pos, hv = _positions_data(cfg, broker, provider)
    A.record_equity(cfg.state_dir, d, broker.get_cash_balance(), holdings_value_krw(cfg.state_dir, hv), _seed(cfg),
                    positions_exist=bool(pos))
    m = A.compute_metrics(A.load_closed(cfg.state_dir), A.load_equity(cfg.state_dir), _seed(cfg))
    blocked = activity.get("blocked", [])
    cash = broker.get_cash_balance()

    fields = _holdings_fields(pos)
    act = f"매수 {activity.get('bought',0)} · 매도 {activity.get('sold',0)} · 차단 {len(blocked)}"
    fields.append({"name": "🔄 오늘 활동", "value": act, "inline": False})
    port = _portfolio_lines(cfg, broker, provider)
    fields.append({"name": "📊 포트폴리오 리스크", "value": "\n".join(port), "inline": False})
    rank_lines = _ranking_lines(signals)
    if rank_lines:
        fields.append({"name": "🏁 오늘 후보 순위", "value": "\n".join(rank_lines)[:1024], "inline": False})
    blk_lines = _blocked_lines(blocked)
    if blk_lines:
        fields.append({"name": "🚫 차단 종목(추적ON)", "value": "\n".join(blk_lines)[:1024], "inline": False})
    watch_lines = _watch_lines(signals)
    if watch_lines:
        fields.append({"name": "👀 WATCH(내일 주목)", "value": "\n".join(watch_lines)[:1024], "inline": False})
    fields.append({"name": "🔬 백테스트 현실성", "value": "\n".join(_realism_lines(cfg)), "inline": False})
    # 🧙 헤르메스 코칭 (오늘 수정할 로직)
    from .coach import daily_coaching
    comp = A.compound_projection(A.load_equity(cfg.state_dir), _seed(cfg))
    coaching = daily_coaching(cfg, positions=pos, activity=activity, metrics=m, compound=comp)
    if coaching:
        fields.append({"name": "🧙 헤르메스 코칭", "value": coaching[:1024], "inline": False})
    adopted = str(cfg.get("regime", "adopted_version", default="v5") or "v5")
    logic_note = {"v7": "추세추종 — 5일선/대량음봉까지 홀딩"}.get(adopted, "")
    logic_tag = f"🧬 적용 로직 {adopted}" + (f" ({logic_note})" if logic_note else "")
    embed = {
        "title": f"📅 스윙 데일리 브리핑 · {d}", "color": BLUE,
        "description": f"계좌: 현금 {_won(cash)} · 평가금 **{_won(cash+hv)}**원\n" + "\n".join(_metrics_lines(m)),
        "fields": fields[:24],
        "footer": {"text": f"{logic_tag} · 페이퍼(가상계좌)"},
    }
    md = _frontmatter("스윙데일리", d) + (
        f"## 📅 데일리 · {d}\n> {logic_tag}\n\n계좌: 현금 {_won(cash)} · 평가금 {_won(cash+hv)}원\n\n"
        "**📊 성과**\n- " + "\n- ".join(_metrics_lines(m)) + "\n\n"
        "**💼 보유 종목 & 복기**\n" + _holdings_md(pos) + f"\n\n**🔄 오늘 활동**: {act}\n\n"
        "**📊 포트폴리오 리스크**\n- " + "\n- ".join(port) + "\n\n"
        + ("**🏁 오늘 후보 순위**\n" + "\n".join(rank_lines) + "\n\n" if rank_lines else "")
        + ("**🚫 차단 종목(추적ON)**\n" + "\n".join(blk_lines) + "\n\n" if blk_lines else "")
        + ("**👀 WATCH**\n" + "\n".join(watch_lines) + "\n\n" if watch_lines else "")
        + "**🔬 백테스트 현실성**\n- " + "\n- ".join(_realism_lines(cfg)) + "\n"
    )
    if coaching:
        md += f"\n**🧙 헤르메스 코칭**\n{coaching}\n"
    return embed, md


# ── Weekly ──
def weekly_brief(cfg: Config, broker, provider, bt_summary=None) -> tuple[dict, str]:
    d = today_kst().isoformat()
    pos, hv = _positions_data(cfg, broker, provider)
    seed = _seed(cfg)
    equity = A.load_equity(cfg.state_dir)
    m = A.compute_metrics(A.load_closed(cfg.state_dir), equity, seed)
    week_change = None
    week_won = 0.0
    if len(equity) >= 2:
        recent = equity[-min(5, len(equity)):]
        week_won = recent[-1]["equity"] - recent[0]["equity"]
        week_change = round(week_won / seed * 100, 2)
    target = float(cfg.get("capital", "weekly_target", default=300000))
    tgt_pct = round(week_won / target * 100, 0) if target else None
    tgt_line = f"🎯 주간목표 {target:,.0f}원 대비: {week_won:+,.0f}원 ({tgt_pct if tgt_pct is not None else '—'}%)"
    fields = [{"name": "🎯 주간 목표", "value": tgt_line.replace("🎯 주간목표 ", "").replace(" 대비:", " →"),
               "inline": False}] + list(_holdings_fields(pos))
    bt_line = ""
    if bt_summary is not None:
        bt_line = (f"종목 {bt_summary.n_stocks} · 거래 {bt_summary.total_trades} · "
                   f"평균승률 {bt_summary.avg_win_rate if bt_summary.avg_win_rate is not None else '—'}% "
                   f"(실데이터 {bt_summary.real_ratio or 0:.0f}%)")
        fields.append({"name": "🧪 백테스트(과거 검증, 20일선 눌림 규칙)",
                       "value": bt_line + "\n→ 페이퍼(실적)와 비교해 규칙 타당성 점검", "inline": False})
    comp = _compound_line(cfg)
    embed = {
        "title": f"📈 스윙 위클리 브리핑 · {d}", "color": PURPLE,
        "description": f"주간 변화 **{_pct(week_change)}** · 누적 {_pct(m.return_pct)}\n"
                       + (comp + "\n" if comp else "") + "\n".join(_metrics_lines(m)),
        "fields": fields,
    }
    md = _frontmatter("스윙위클리", d) + (
        f"## 📈 위클리 · {d}\n주간 변화 {_pct(week_change)} · 누적 {_pct(m.return_pct)}\n"
        f"{tgt_line}\n{comp}\n\n"
        "**📊 주간 성과**\n- " + "\n- ".join(_metrics_lines(m)) + "\n\n"
        "**💼 보유 종목 & 복기**\n" + _holdings_md(pos) + "\n"
    )
    if bt_line:
        md += f"\n**🧪 백테스트(과거 검증)**: {bt_line}\n"
    return embed, md


# ── Monthly (로직 수정 제안) ──
def monthly_report(cfg: Config, broker, provider) -> tuple[dict, str]:
    d = today_kst().isoformat()
    closed = A.load_closed(cfg.state_dir)
    m = A.compute_metrics(closed, A.load_equity(cfg.state_dir), _seed(cfg))
    sugg = _logic_suggestions(cfg, m, closed)
    comp = _compound_line(cfg)
    embed = {
        "title": f"🧭 스윙 먼슬리 로직 보고서 · {d}", "color": 0xF1C40F,
        "description": (comp + "\n\n" if comp else "") + "**한 달 성과**\n" + "\n".join(_metrics_lines(m)),
        "fields": [{"name": "🔧 로직 수정 제안 (다음 달 검토)", "value": "\n".join(sugg)[:1024], "inline": False}],
    }
    md = _frontmatter("스윙먼슬리로직", d) + (
        f"## 🧭 먼슬리 로직 보고서 · {d}\n\n{comp}\n\n**📊 한 달 성과**\n- " + "\n- ".join(_metrics_lines(m)) +
        "\n\n**🔧 로직 수정 제안 (다음 달 적용 검토)**\n" + "\n".join(sugg) +
        "\n\n_근거: 성과·점수대별 승률·실패 패턴. config.yaml 수치로 반영 가능._\n"
    )
    return embed, md


def _logic_suggestions(cfg: Config, m: A.Metrics, closed: list[dict]) -> list[str]:
    s: list[str] = []
    if m.n_closed < 5:
        return ["- 표본 부족(청산 5건 미만) — 통계적 결론 보류, 데이터 더 축적."]
    th = cfg.get("scoring", "thresholds", default={})
    small_ok = float(th.get("small_ok", 70))
    band = m.by_score_band
    if "70-79" in band and "80+" in band:
        if band["70-79"]["win_rate"] < 45 and band["80+"]["win_rate"] >= 55:
            s.append(f"- **진입 임계값 상향**: 70-79점 승률({band['70-79']['win_rate']:.0f}%) 낮음 → "
                     f"매수 최소점수 {small_ok:.0f}→75 검토.")
    if m.win_rate is not None and m.win_rate < 40:
        s.append(f"- **진입 보수화**: 승률 {m.win_rate}% 낮음 → 거래량/타점 조건 또는 시장필터 강화.")
    if m.stop_hit and m.target_hit and m.stop_hit > m.target_hit * 1.5:
        s.append(f"- **손절 과다**(손절 {m.stop_hit} vs 목표 {m.target_hit}): 손절폭 -3%→-3.5~4% 또는 진입 타이밍 개선.")
    if m.profit_factor is not None and m.profit_factor < 1.0:
        s.append(f"- **PF {m.profit_factor}<1(순손실)**: 익절 +5→+6% 또는 손실거래 차단룰 추가.")
    if m.avg_hold_days is not None and m.avg_hold_days >= 5:
        s.append(f"- **보유 장기화**(평균 {m.avg_hold_days}일): 모멘텀 약화 매도 더 빠르게.")
    if m.rule_adherence is not None and m.rule_adherence < 95:
        s.append(f"- **손절 준수율 {m.rule_adherence}%**: 손절 자동화 점검.")
    return s or ["- 현재 로직 양호 — 큰 수정 불요. 임계값 미세조정만 관찰."]


def _frontmatter(typ: str, d: str) -> str:
    return f"---\ntype: {typ}\n날짜: {d}\ntags: [스윙, 브리핑]\n---\n> 생성 {now_kst():%Y-%m-%d %H:%M}\n\n"
