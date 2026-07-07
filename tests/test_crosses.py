"""골든/데드 크로스 판정·랭킹·타점·매칭 — 합성 시계열, look-ahead 없음."""
import numpy as np
import pandas as pd

from swing_trader.market.crosses import detect_cross, scan, match_names, _entry


def _df(closes, vols=None):
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    vols = np.asarray(vols if vols is not None else np.full(n, 1e6), dtype=float)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"open": closes, "high": closes * 1.01,
                         "low": closes * 0.99, "close": closes, "volume": vols}, index=idx)


def _slice_to_first_cross(closes, kind):
    """크로스가 '마지막 봉'에 오도록 시계열을 잘라 반환(없으면 AssertionError)."""
    c = pd.Series(closes, dtype=float)
    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    for i in range(201, len(c)):
        up = ma50.iloc[i - 1] <= ma200.iloc[i - 1] and ma50.iloc[i] > ma200.iloc[i]
        dn = ma50.iloc[i - 1] >= ma200.iloc[i - 1] and ma50.iloc[i] < ma200.iloc[i]
        if (kind == "golden" and up) or (kind == "dead" and dn):
            return closes[: i + 1]
    raise AssertionError(f"{kind} cross not found in synthetic series")


GOLDEN = list(np.linspace(120, 60, 260)) + list(np.linspace(60, 160, 120))
DEAD = list(np.linspace(60, 160, 260)) + list(np.linspace(160, 50, 120))


def test_golden_cross_detected_on_last_bar():
    g = _slice_to_first_cross(GOLDEN, "golden")
    m = detect_cross(_df(g, np.full(len(g), 1e8)))
    assert m is not None and m["kind"] == "golden"
    assert m["ma50"] > m["ma200"]
    assert m["vol_ratio"] > 0


def test_dead_cross_detected_on_last_bar():
    m = detect_cross(_df(_slice_to_first_cross(DEAD, "dead")))
    assert m is not None and m["kind"] == "dead"
    assert m["ma50"] < m["ma200"]


def test_no_cross_when_stable_uptrend():
    assert detect_cross(_df(list(np.linspace(50, 200, 260)))) is None


def test_needs_205_bars():
    assert detect_cross(_df(list(np.linspace(100, 120, 100)))) is None


def test_entry_targets_use_ma_levels():
    e = _entry({"ma50": 100.0, "ma200": 90.0, "close": 105.0, "high120": 130.0})
    assert e["buyLow"] == 100.0 and e["buyHigh"] == 105.0   # 50일선~현재가
    assert e["stop"] == 90.0                                # 200일선(이탈=무효)
    assert e["target"] == 130.0                             # max(+15%=120.75, 120일 고점 130)


def test_scan_splits_ranks_and_filters_liquidity():
    g = _slice_to_first_cross(GOLDEN, "golden")
    d = _slice_to_first_cross(DEAD, "dead")
    kr = {"000010": _df(g, np.full(len(g), 1e8)), "000020": _df(d, np.full(len(d), 1e8))}
    res = scan(kr, {}, {}, {"000010": "골든주", "000020": "데드주"})
    assert [x["name"] for x in res["golden"]] == ["골든주"]
    assert [x["name"] for x in res["dead"]] == ["데드주"]
    # 유동성 하한(거래대금 50억) 미달은 제외
    low = {"000030": _df(g, np.full(len(g), 1.0))}
    assert scan(low, {}, {}, {"000030": "저유동"})["golden"] == []


def test_match_names_by_name_and_ticker():
    items = [{"ticker": "005930", "name": "삼성전자", "market": "KR"},
             {"ticker": "AAPL", "name": "애플", "market": "US"}]
    assert [m["ticker"] for m in match_names(items, {"삼성전자"}, {})] == ["005930"]
    assert [m["ticker"] for m in match_names(items, {"애플"}, {"애플": "AAPL"})] == ["AAPL"]
    assert match_names(items, {"카카오"}, {}) == []
