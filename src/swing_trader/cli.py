"""CLI — swing-trader scan | run-once | review | backtest | doctor."""
from __future__ import annotations

import argparse
import sys

from . import main as M
from .config import load_config


def _utf8_console() -> None:
    """Windows cp949 콘솔에서 이모지/한글 출력이 깨지지 않게 UTF-8 로 전환."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _print_doctor(res: dict) -> None:
    print("\n🩺 swing-trader doctor\n" + "=" * 40)
    for name, ok, detail in res["checks"]:
        mark = "✅" if ok else "❌"
        print(f"{mark} {name:<22} {detail}")
    print("=" * 40)
    if res["live_allowed"]:
        print("⚠️  실전 주문이 허용된 상태입니다. 의도한 것이 아니면 .env 를 확인하세요.")
    else:
        print("🛡️  실전 주문 차단됨 — 페이퍼 트레이딩 모드(안전).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swing-trader", description="단기 스윙 트레이딩 자동화(페이퍼 기본)")
    ap.add_argument("--config", default=None, help="config.yaml 경로")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="처리 종목 수 제한(데모/테스트)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="후보 점수화 → Signals.md")
    sc.add_argument("--market", choices=["all", "kr", "us"], default="all")
    ro = sub.add_parser("run-once", help="scan + 페이퍼 주문 실행 → Trade.md")
    ro.add_argument("--market", choices=["all", "kr", "us"], default="all",
                    help="kr=한국(아침 시가) · us=미국(새벽 마감)")
    ro.add_argument("--no-brief", action="store_true", help="Daily 브리핑 건너뜀(시장 분리 실행 시 1회만)")
    sub.add_parser("review", help="거래 로그 분석 → Review.md")
    bt = sub.add_parser("backtest", help="간단 백테스트 → Backtest.md")
    bt.add_argument("--days", type=int, default=60)
    br = sub.add_parser("brief", help="주간/월간 브리핑(디스코드+볼트). auto=금요일 주간·월 마지막금 월간")
    br.add_argument("--period", choices=["auto", "daily", "weekly", "monthly"], default="auto")
    lg = sub.add_parser("logic", help="로직 버전 스냅샷 + 이전과 A/B 백테스트 → 변경이력 기록")
    lg.add_argument("--note", required=True, help="변경 사유(예: '익절 5→6%, 손절 -3→-3.5%')")
    sub.add_parser("logic-review", help="AI 로직 진단(매매일지·로그 근거 문제점+수정안) → 04_Trading/Logic")
    sub.add_parser("doctor", help="환경/경로/키 점검")
    cd = sub.add_parser("check-done", help="오늘 해당 시장 런 완료 마커 있으면 exit 0, 없으면 1")
    cd.add_argument("--market", choices=["kr", "us", "scalp_kr", "scalp_us"], required=True)
    nf = sub.add_parser("notify-failover", help="로컬 미실행 → 클라우드 대체 경고를 swing 채널로 발송")
    nf.add_argument("--markets", required=True, help='공백 구분, 예: "kr us"')
    sub.add_parser("harness", help="현재 로직 IS/OOS 성과 측정(검증 하니스) → 볼트+디스코드")
    sub.add_parser("versions", help="버전별 백테스트 리플레이 → state/version_compare.json(대시보드 비교화면)")
    sub.add_parser("scalp-compare", help="단타 v1/v2 백테스트 리플레이 → state/scalp_compare.json")
    sub.add_parser("v6-compare", help="v4/v5/v6 동일조건 regime 비교 → state/v6_compare.json + 볼트문서")
    sr = sub.add_parser("scalp-run", help="단타 페이퍼 1사이클(이전 계획 정산+오늘 계획) → 디스코드 ⚡")
    sr.add_argument("--market", choices=["kr", "us"], required=True)

    args = ap.parse_args(argv)
    _utf8_console()
    M._setup_logging(args.verbose)
    cfg = load_config(args.config)

    if args.cmd == "doctor":
        _print_doctor(M.run_doctor(cfg))
        return 0
    if args.cmd == "scan":
        signals, path = M.run_scan(cfg, limit=args.limit, market=args.market)
        print(f"✅ 신호 {len(signals)}건 → {path}")
        return 0
    if args.cmd == "run-once":
        if not cfg.safety.paper_trading and cfg.safety.live_allowed:
            print("⚠️  실전 주문 허용 상태. 계속하려면 LIVE 환경을 확인하세요.")
        r = M.run_once(cfg, limit=args.limit, market=args.market, do_brief=not args.no_brief)
        print(f"✅ run-once[{args.market}]: 매수 {r['bought']} · 매도 {r['sold']} · 차단 {len(r['blocked'])}")
        print(f"   현금 {r['cash']:,.0f}원 · 실현손익 {r['realized']:,.0f}원")
        print(f"   신호 {r['signals']}")
        if r["trades"]:
            print(f"   거래로그 {r['trades']}")
        return 0
    if args.cmd == "review":
        path = M.run_review(cfg)
        print(f"✅ 리뷰 → {path}")
        return 0
    if args.cmd == "backtest":
        path = M.run_backtest(cfg, days=args.days, limit=args.limit or 8)
        print(f"✅ 백테스트({args.days}일) → {path}")
        return 0
    if args.cmd == "brief":
        sent = M.run_brief(cfg, args.period)
        print(f"✅ brief({args.period}): {', '.join(sent) or '오늘 해당 없음'}")
        return 0
    if args.cmd == "logic":
        r = M.run_logic(cfg, args.note)
        print(f"✅ 로직 v{r['version']} 기록 → {r['path']} (변경 {len(r['changes'])}건)")
        if r["ab"] and r["ab"][0].avg_win_rate is not None:
            print(f"   A/B 백테스트 승률: v{r['prev_v']} {r['ab'][0].avg_win_rate}% → v{r['version']} {r['ab'][1].avg_win_rate}%")
        return 0
    if args.cmd == "logic-review":
        r = M.run_logic_review(cfg)
        print(f"✅ AI 로직 진단 → {r['path']}{' (디스코드 발송)' if r['sent'] else ''}")
        return 0
    if args.cmd == "check-done":
        from .state import daily_marker as DM
        return 0 if DM.is_done(cfg.state_dir, args.market, DM.today_kst()) else 1
    if args.cmd == "harness":
        path = M.run_harness(cfg)
        print(f"✅ 하니스 측정 → {path}")
        return 0
    if args.cmd == "versions":
        path = M.run_version_compare(cfg)
        print(f"✅ 버전 비교 데이터 → {path}")
        return 0
    if args.cmd == "scalp-compare":
        path = M.run_scalp_compare(cfg)
        print(f"✅ 단타 비교 데이터 → {path}")
        return 0
    if args.cmd == "v6-compare":
        path = M.run_v6_compare(cfg)
        print(f"✅ v6 비교 데이터 → {path}")
        return 0
    if args.cmd == "scalp-run":
        r = M.run_scalp(cfg, args.market)
        print(f"✅ scalp-run[{args.market}]: 정산 {r['settled']}건 · 계획 {r['planned']}건"
              + (" ⚠️경고 발생" if r["warned"] else "")
              + (" ⏸ 정산 보류(다음 런 재시도)" if r.get("held") else ""))
        return 0
    if args.cmd == "notify-failover":
        from .notify.discord import notify
        from .state import daily_marker as DM
        ts = DM.today_kst().isoformat()
        mk = " ".join(m.upper() for m in args.markets.split())
        msg = (f"⚠️ 로컬 스윙 미실행 감지 — 클라우드가 [{mk}] 대체 처리함. "
               f"노트북(로컬 Swing 작업) 점검 요망. ({ts} KST)")
        notify(cfg.creds.discord_webhook_url, msg)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
