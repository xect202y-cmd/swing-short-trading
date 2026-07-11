"""v10 시황 게이트 + per-ticker/전시장 오케스트레이션."""
from swing_trader.strategy import v10_new_high as v10


def test_regime_ok_by_market():
    kospi_up = {"2026-07-08", "2026-07-09"}
    kosdaq_up = {"2026-07-09"}
    assert v10.regime_ok("KOSPI", "2026-07-09", kospi_up, kosdaq_up) is True
    assert v10.regime_ok("KOSPI", "2026-07-07", kospi_up, kosdaq_up) is False
    assert v10.regime_ok("KOSDAQ", "2026-07-08", kospi_up, kosdaq_up) is False
    assert v10.regime_ok("KOSDAQ", "2026-07-09", kospi_up, kosdaq_up) is True


def test_regime_ok_fail_open_when_no_data():
    assert v10.regime_ok("KOSDAQ", "2026-07-09", None, None) is True
