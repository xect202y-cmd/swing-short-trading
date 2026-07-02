"""실시간 시세 파서 — 네이버(KR)/야후(US) 응답 파싱(네트워크 없음)."""
from swing_trader.market.realtime import RealtimeQuote, _parse_naver, _parse_yahoo


def test_parse_naver_comma_strings():
    j = {"datas": [{"closePrice": "61,300", "openPrice": "60,900",
                    "compareToPreviousClosePrice": "400"}]}
    q = _parse_naver(j)
    assert q == RealtimeQuote(price=61300.0, open=60900.0, prev_close=60900.0, source="naver")
    # prev_close = price - compare(400) = 60,900


def test_parse_naver_bad_payload_returns_none():
    assert _parse_naver({}) is None
    assert _parse_naver({"datas": [{"closePrice": "0"}]}) is None


def test_parse_yahoo_converts_fx():
    j = {"chart": {"result": [{"meta": {
        "regularMarketPrice": 100.0, "chartPreviousClose": 98.0, "regularMarketDayHigh": 101.0,
    }, "indicators": {"quote": [{"open": [99.0]}]}}]}}
    q = _parse_yahoo(j, fx=1400.0)
    assert q.price == 140000.0 and q.open == 138600.0 and q.prev_close == 137200.0
    assert q.source == "yahoo"


def test_parse_yahoo_missing_returns_none():
    assert _parse_yahoo({"chart": {"result": []}}, fx=1400.0) is None
