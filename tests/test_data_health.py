"""데이터 수집 건전성 판정(assess) + 실패 경고(alert) 테스트."""
from swing_trader.notify import health as H


def test_assess_empty_is_failure():
    r = H.assess([])
    assert r.ok is False
    assert r.n_total == 0 and r.n_real == 0
    assert "0건" in r.reason


def test_assess_all_synthetic_is_failure():
    r = H.assess(["synthetic", "synthetic", None])
    assert r.ok is False
    assert r.n_total == 2 and r.n_real == 0
    assert "synthetic" in r.reason


def test_assess_some_real_is_ok():
    r = H.assess(["yfinance", "synthetic", "pykrx"])
    assert r.ok is True
    assert r.n_total == 3 and r.n_real == 2


def test_alert_sends_to_webhook(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "swing_trader.notify.discord.notify",
        lambda url, content: (sent.update(url=url, content=content), True)[1],
    )
    ok = H.alert("http://hook", "주간 백테스트", "실데이터 0건")
    assert ok is True
    assert sent["url"] == "http://hook"
    assert "주간 백테스트" in sent["content"] and "실데이터 0건" in sent["content"]
    assert "⚠️" in sent["content"]
