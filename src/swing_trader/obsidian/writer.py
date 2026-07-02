"""볼트 쓰기 — Signals/Trade/Review/Backtest 마크다운. 모든 판단을 사람이 읽게 기록."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..config import Config
from ..models import Order, Signal, SignalKind, now_kst
from ..state.daily_marker import today_kst


def _won(v: float | None) -> str:
    return "—" if v is None else f"{round(v):,}원"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


class VaultWriter:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _path(self, dir_key: str, suffix: str, d: date | None = None) -> Path:
        d = d or today_kst()
        dpath = self.cfg.write_dir(dir_key)
        dpath.mkdir(parents=True, exist_ok=True)
        return dpath / f"{d.isoformat()}_{suffix}.md"

    # ── 신호 ──
    def write_signals(self, signals: list[Signal], d: date | None = None) -> Path:
        d = d or today_kst()
        lines = [
            "---", "type: 스윙신호", f"날짜: {d.isoformat()}", "tags: [스윙, 신호]", "---",
            f"# 📡 스윙 신호 · {d.isoformat()}",
            f"> 생성 {now_kst():%Y-%m-%d %H:%M} · 후보 {len(signals)}건",
            "",
        ]
        order = {SignalKind.BUY: 0, SignalKind.BUY_WATCH: 1, SignalKind.SELL: 2,
                 SignalKind.HOLD: 3, SignalKind.BLOCKED: 4}
        for s in sorted(signals, key=lambda x: (order.get(x.kind, 9), -x.score)):
            p = s.plan
            lines += [
                f"## [{s.kind.value}] {s.name} ({s.ticker}) · {s.score:.0f}점",
                f"- 현재가: {_won(s.price)}",
                f"- 매수구간/손절/목표: {_won(p.entry) if p else '—'} / "
                f"{_won(p.stop) if p else '—'} / {_won(p.target1) if p else '—'}"
                + (f" / {_won(p.target2)}" if p and p.target2 else ""),
                f"- 손익비: {p.reward_risk if p and p.reward_risk else '—'} "
                f"(위험 {_pct(p.risk_pct) if p else '—'} · 보상 {_pct(p.reward1_pct) if p else '—'})",
                f"- 거시 리스크: {s.macro_risk.value} · 이벤트 리스크: {s.event_risk.value}",
                "- 기술적 근거: " + ("; ".join(s.reasons) if s.reasons else "—"),
            ]
            if s.blocked_reasons:
                lines.append("- ⛔ 차단/주의: " + "; ".join(s.blocked_reasons))
            if s.breakdown:
                lines.append("- 점수 내역:")
                lines += [f"  {ln}" for ln in s.breakdown.as_lines()]
            lines.append("")
        path = self._path("signals_dir", "Signals", d)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    # ── 거래 로그 ──
    def append_trades(self, orders: list[Order], d: date | None = None) -> Path:
        d = d or today_kst()
        path = self._path("logs_dir", "Trade", d)
        new = not path.exists()
        out: list[str] = []
        if new:
            out += [
                "---", "type: 스윙거래로그", f"날짜: {d.isoformat()}", "tags: [스윙, 거래]", "---",
                f"# 🧾 거래 로그 · {d.isoformat()}", "",
                "| 시각 | 주문ID | 종목 | 매매 | 수량 | 체결가 | 수수료+슬리피지 | 손절 | 목표 | 상태 | 사유 |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        for o in orders:
            out.append(
                f"| {o.created_at:%H:%M:%S} | {o.order_id} | {o.ticker} | {o.side} | {o.quantity} | "
                f"{_won(o.filled_price or o.price)} | {round(o.fee + o.slippage):,}원 | "
                f"{'' if not o.note else ''}{_won(getattr(o, 'stop', None))} | {_won(getattr(o, 'target', None))} | "
                f"{o.status} | {o.note or '—'} |".replace("None원", "—")
            )
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return path

    def write_review(self, content: str, d: date | None = None) -> Path:
        d = d or today_kst()
        path = self._path("reviews_dir", "Review", d)
        path.write_text(content, encoding="utf-8")
        return path

    def write_backtest(self, content: str, d: date | None = None) -> Path:
        d = d or today_kst()
        path = self._path("backtests_dir", "Backtest", d)
        path.write_text(content, encoding="utf-8")
        return path

    def write_harness(self, content: str, d: date | None = None) -> Path:
        d = d or today_kst()
        path = self._path("backtests_dir", "Harness", d)
        path.write_text(content, encoding="utf-8")
        return path

    def write_logic(self, content: str, vnum: int) -> Path:
        dpath = self.cfg.write_dir("logic_dir")
        dpath.mkdir(parents=True, exist_ok=True)
        path = dpath / f"{today_kst().isoformat()}_v{vnum}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def write_logic_review(self, content: str) -> Path:
        dpath = self.cfg.write_dir("logic_dir")
        dpath.mkdir(parents=True, exist_ok=True)
        path = dpath / f"{today_kst().isoformat()}_AI진단.md"
        path.write_text(content, encoding="utf-8")
        return path

    def append_logic_changelog(self, line: str) -> Path:
        dpath = self.cfg.write_dir("logic_dir")
        dpath.mkdir(parents=True, exist_ok=True)
        path = dpath / "📒 로직 변경 이력.md"
        if not path.exists():
            path.write_text("---\ntype: 스윙로직이력\ntags: [스윙, 로직]\n---\n"
                            "# 📒 스윙 로직 변경 이력\n> 로직을 바꿀 때마다 자동 기록(버전·사유·A/B 승률).\n\n",
                            encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return path

    def write_daily(self, content: str, d: date | None = None) -> Path:
        path = self._path("briefs_dir", "Daily", d or today_kst())
        path.write_text(content, encoding="utf-8")
        return path

    def write_weekly(self, content: str, d: date | None = None) -> Path:
        path = self._path("briefs_dir", "Weekly", d or today_kst())
        path.write_text(content, encoding="utf-8")
        return path

    def write_monthly(self, content: str, d: date | None = None) -> Path:
        d = d or today_kst()
        dpath = self.cfg.write_dir("reports_dir")
        dpath.mkdir(parents=True, exist_ok=True)
        path = dpath / f"{d.strftime('%Y-%m')}_Logic.md"
        path.write_text(content, encoding="utf-8")
        return path
