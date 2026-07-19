"""equity_history.json 과거 오염행 1회성 재계산(P3-1d, 멱등).

배경: v10_live.py의 옛 record_equity 호출이 US 슬리브(v1_us_state.json)를 누락해,
공유현금 풀에서 US 매수 이후에도 holdings=0(=equity==cash)으로 기록된 행이 있었다
(가짜 폭락, 예 2026-07-07 −39.99%). review/briefer.holdings_value_krw + record_equity
방어가드(P3-1a/1b)가 향후 재발은 막지만, 이미 찍힌 과거 행은 별도 보정이 필요하다.

무엇을 보정하는가:
  holdings==0(또는 NaN)인 행 중, v1_us_state.json 의 equity_hist 스냅샷에 **정확히 같은
  날짜**가 있으면 그 시점 US 단독 마크가치를 역산해(equity_hist 값 − 그 시점까지의
  누적실현손익) holdings/equity 에 더한다.

무엇을 보정하지 못하는가(지어내지 않음):
  equity_hist 는 US 사이클이 실제로 돈 날짜만 스냅샷을 남긴다(스파스). 문제행의 날짜에
  스냅샷이 없으면 그날 US 포지션의 실제 종가·환율 마크가 어디에도 남아있지 않아 복원
  불가능하다 — 이런 행은 건드리지 않고 목록으로 보고만 한다(향후 오염은 가드가 차단).

사용:
  .venv/Scripts/python.exe scripts/recompute_equity.py            # 미리보기(기본, 파일 안 건드림)
  .venv/Scripts/python.exe scripts/recompute_equity.py --apply    # 실제 반영(백업 후 덮어씀)
  .venv/Scripts/python.exe scripts/recompute_equity.py --state-dir PATH --seed 5000000
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _load(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _is_zero_or_nan(v) -> bool:
    if v is None:
        return True
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return True
    return fv == 0.0 or fv != fv   # fv != fv → NaN


def _realized_krw_as_of(trades: list[dict], d: str) -> float:
    """US 슬리브 누적 실현손익(그 날짜까지 청산된 거래만 합산)."""
    return sum(float(t.get("pnl_krw", 0) or 0) for t in trades if str(t.get("exit_date", "")) <= d)


def _us_hold_as_of(us_state: dict, d: str) -> float | None:
    """날짜 d 의 US 단독 마크가치(원) 역산. equity_hist 에 그 날짜 스냅샷이 없으면 None(복원 불가).

    equity_hist 항목 = 실현손익누적 + US 마크가치(그 시점 스냅샷). 그 시점까지의 실현손익은
    trades(exit_date≤d) 로 재구성 가능하므로 빼면 마크가치만 남는다.
    """
    hit = next((e for e in us_state.get("equity_hist", []) if e.get("date") == d), None)
    if hit is None:
        return None
    realized_asof = _realized_krw_as_of(us_state.get("trades", []), d)
    return float(hit["equity"]) - realized_asof


def recompute(state_dir: Path, seed: float, apply: bool = False) -> dict:
    eq_path = state_dir / "equity_history.json"
    rows = _load(eq_path)
    if not rows:
        return {"fixed": [], "unrecoverable": [], "note": f"{eq_path} 없음/빈파일 — 할 일 없음"}
    us_state = _load(state_dir / "v1_us_state.json") or {}

    fixed: list[str] = []
    unrecoverable: list[str] = []
    for row in rows:
        if not _is_zero_or_nan(row.get("holdings")):
            continue
        d = row.get("date", "")
        us_hold = _us_hold_as_of(us_state, d)
        if us_hold is None:
            unrecoverable.append(d)
            continue
        if us_hold <= 1:
            continue   # 역산값도 사실상 0 — 원래 기록이 맞음(버그 아님), 건드릴 필요 없음
        new_holdings = round((row.get("holdings") or 0) + us_hold, 0)
        new_equity = round(row["cash"] + new_holdings, 0)
        row["holdings"] = new_holdings
        row["equity"] = new_equity
        row["return_pct"] = round((new_equity - seed) / seed * 100, 2)
        fixed.append(d)

    if apply and fixed:
        bak_path = eq_path.with_suffix(".json.bak")
        if not bak_path.exists():   # 멱등: 이미 백업 있으면 원본(최초 상태) 보존, 덮지 않음
            shutil.copy2(eq_path, bak_path)
        eq_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"fixed": fixed, "unrecoverable": unrecoverable, "applied": bool(apply and fixed)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="recompute_equity",
        description="equity_history.json 의 US 슬리브 누락 과거행을 복원 가능한 만큼만 보정(1회성, 멱등).")
    ap.add_argument("--state-dir", default=None, help="state 디렉터리 경로(생략 시 config.yaml paths.state_dir)")
    ap.add_argument("--config", default=None, help="config.yaml 경로(seed 기본값 조회용)")
    ap.add_argument("--seed", type=float, default=None, help="시드금(원). 생략 시 config capital.seed")
    ap.add_argument("--apply", action="store_true", help="실제로 파일을 덮어씀(기본은 미리보기만, 안 건드림)")
    args = ap.parse_args(argv)

    from swing_trader.config import load_config
    cfg = load_config(args.config)
    state_dir = Path(args.state_dir) if args.state_dir else cfg.state_dir
    seed = args.seed if args.seed is not None else float(cfg.get("capital", "seed", default=5_000_000))

    res = recompute(state_dir, seed, apply=args.apply)
    if res.get("note"):
        print(res["note"])
        return 0
    mode = "적용됨(백업 .json.bak)" if res["applied"] else "미리보기만(--apply 로 실제 반영)"
    print(f"보정 {len(res['fixed'])}건 [{mode}]: {res['fixed'] or '없음'}")
    print(f"복원 불가(스냅샷 없음, 건드리지 않음) {len(res['unrecoverable'])}건: {res['unrecoverable'] or '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
