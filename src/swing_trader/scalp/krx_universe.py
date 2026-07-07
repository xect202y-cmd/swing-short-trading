"""KRX 전시장 유니버스 — v5 상따 백테스트용 등락률 상위 전시장 스캔.

pykrx의 전종목 스냅샷은 KRX 로그인이 필요해져(2025~) 사용 불가 →
FinanceDataReader(네이버 기반, 무로그인)로 ①상장 목록 ②종목별 일봉을 수집한다.
- 유니버스: KOSPI+KOSDAQ 보통주(코드 끝 '0'), 스팩 제외
- 캐시: state/krx_panel.pkl (로컬 아티팩트, gitignore) — 종목별 증분·재개 가능
- 주의: 네이버 일봉은 수정주가라 액면분할 부근 등락률에 노이즈 가능(희귀, 허용)
"""
from __future__ import annotations

import pickle
import time
from pathlib import Path

_CACHE = "krx_panel.pkl"
_META_DAYS = 620  # 달력일 — 거래일 ~420 확보(백테스트 500봉 여유는 종목별 tail 로)


def list_universe() -> list[dict]:
    """KRX 보통주 목록 — [{code, name, market}]. 스팩·KONEX·우선주 제외."""
    import FinanceDataReader as fdr
    li = fdr.StockListing("KRX")
    out = []
    for _, r in li.iterrows():
        code, name, mk = str(r["Code"]), str(r["Name"]), str(r["Market"])
        if not code.endswith("0") or mk not in ("KOSPI", "KOSDAQ"):
            continue
        if "스팩" in name:
            continue
        out.append({"code": code, "name": name, "market": mk})
    return out


def load_cache(state_dir: Path) -> dict:
    p = Path(state_dir) / _CACHE
    if not p.exists():
        return {}
    try:
        return pickle.load(open(p, "rb"))
    except Exception:  # noqa: BLE001 — 손상 캐시는 재수집
        return {}


def save_cache(state_dir: Path, panel: dict) -> None:
    pickle.dump(panel, open(Path(state_dir) / _CACHE, "wb"))


def _fetch_one(code: str, start: str):
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader(code, start)
        if df is None or len(df) < 90:
            return code, None   # 신규상장 등 — 재시도 방지 마커
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                "Close": "close", "Volume": "volume"})
        return code, df[["open", "high", "low", "close", "volume"]].astype(float)
    except Exception:  # noqa: BLE001 — 개별 실패는 마커 후 계속
        return code, None


def fetch_panel(state_dir: Path, progress_path: Path | None = None,
                workers: int = 8, save_every: int = 100) -> dict:
    """종목별 일봉을 병렬 증분 수집(재개 가능). 반환: {code: DataFrame[open..volume]}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import date, timedelta
    start = (date.today() - timedelta(days=_META_DAYS)).isoformat()
    uni = list_universe()
    panel = load_cache(state_dir)
    todo = [u["code"] for u in uni if u["code"] not in panel]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_fetch_one, code, start) for code in todo]
        for fu in as_completed(futs):
            code, df = fu.result()
            panel[code] = df
            done += 1
            if done % save_every == 0:   # 저장은 메인 스레드에서만(피클 원자성)
                save_cache(state_dir, panel)
                if progress_path:
                    progress_path.write_text(f"{done}/{len(todo)}", encoding="utf-8")
    save_cache(state_dir, panel)
    if progress_path:
        progress_path.write_text(f"done {len(todo)}/{len(todo)}", encoding="utf-8")
    return {k: v for k, v in panel.items() if v is not None}


def _kosdaq_up_days(ma: int = 50) -> set | None:
    """v6 국면게이트 — 코스닥(KQ11) 종가 ≥ ma일선인 날짜(YYYY-MM-DD) 집합. 실패 시 None(게이트 미적용=v5와 동일)."""
    try:
        import FinanceDataReader as fdr
        s = fdr.DataReader("KQ11", "2023-06-01")["Close"].astype(float)
        up = s >= s.rolling(ma).mean()
        return {d.strftime("%Y-%m-%d") for d, u in up.items() if bool(u)}
    except Exception:  # noqa: BLE001
        return None


def v6_market_trades(panel: dict, min_tv_eok: float = 50.0,
                     fee_bps: float = 1.5, slip_bps: float = 5.0) -> list:
    """v6 = v5 오버나잇 상따 + 코스닥 50일선 국면게이트(약세국면 진입일 제외). 백테스트: PF·위험조정 개선."""
    return v5_market_trades(panel, min_tv_eok, fee_bps, slip_bps, up_days=_kosdaq_up_days(50))


def v5_market_trades(panel: dict, min_tv_eok: float = 50.0,
                     fee_bps: float = 1.5, slip_bps: float = 5.0,
                     up_days: set | None = None) -> list:
    """전시장 패널에서 v5 '오버나잇 상따' 리플레이(벡터화) → harness.Trade 목록.

    진입 = 폭등 당일(D) 종가(장 마감 동시호가 참여 가정), 청산 = 익일(D+1) 시가.
    - 상한가 잠금(+29.5%↑) 마감은 종가 매수가 불가능(매수잔량 대기)하므로 제외 — 정직 체결.
    - 인트라데이 변형(D+1 시가 매수→당일 청산)은 OOS 644건 누적 -76%로 폐기:
      D+1 시가가 바로 상따 트레이더의 매도 지점이다. 수익의 본체는 오버나잇 갭.
    look-ahead 없음: 셋업은 D일까지의 값만, 청산가는 D+1 확정 시가.
    """
    import numpy as np
    from ..strategy.harness import Trade
    from .strategy import V5_HIGH_N, V5_LIMIT_CAP, V5_SURGE, V5_VOL_X
    cost = (fee_bps + slip_bps) / 10000
    out: list = []
    for code, df in panel.items():
        if df is None or len(df) < V5_HIGH_N + 30:
            continue
        c = df["close"]
        v = df["volume"]
        chg = (c / c.shift(1) - 1) * 100                     # D일 등락률
        vmean = v.shift(1).rolling(20).mean()                # D 제외 직전 20일 평균
        high_n = c >= c.rolling(V5_HIGH_N).max()             # D 종가가 60일 최고
        tv_ok = (c * v / 1e8) >= min_tv_eok
        nxt_open = df["open"].shift(-1)                      # D+1 시가(청산가)
        m = ((chg >= V5_SURGE) & (chg < V5_LIMIT_CAP) & (v >= V5_VOL_X * vmean)
             & high_n & tv_ok & nxt_open.notna() & (nxt_open > 0)).fillna(False).values
        if not m.any():
            continue
        entry = c.values[m] * (1 + cost)
        ex = nxt_open.values[m] * (1 - cost)
        rets = ex / entry - 1
        dates = df.index[m]
        for d, r in zip(dates, rets):
            ds = d.strftime("%Y-%m-%d")
            if up_days is not None and ds not in up_days:
                continue    # v6 국면게이트: 코스닥 약세일 진입 제외
            out.append(Trade(code, ds, float(r)))
    return out
