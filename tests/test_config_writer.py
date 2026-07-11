import pytest
from swing_trader.strategy.config_writer import set_config_value


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_replaces_value_and_preserves_comment(tmp_path):
    p = _write(tmp_path, "risk:\n  take1_pct: 6.0             # 1차 익절\n  require_uptrend: true\n")
    set_config_value(p, "risk.take1_pct", 6.5, expected_current=6.0)
    out = p.read_text(encoding="utf-8")
    assert "take1_pct: 6.5" in out
    assert "# 1차 익절" in out           # 주석 보존
    assert "require_uptrend: true" in out  # 다른 줄 불변


def test_replaces_bool(tmp_path):
    p = _write(tmp_path, "risk:\n  require_uptrend: true   # 추세필터\n")
    set_config_value(p, "risk.require_uptrend", False, expected_current=True)
    assert "require_uptrend: false" in p.read_text(encoding="utf-8")


def test_raises_on_current_mismatch(tmp_path):
    p = _write(tmp_path, "risk:\n  take1_pct: 6.0\n")
    with pytest.raises(ValueError):
        set_config_value(p, "risk.take1_pct", 6.5, expected_current=5.0)


def test_raises_when_key_absent(tmp_path):
    p = _write(tmp_path, "risk:\n  take1_pct: 6.0\n")
    with pytest.raises(ValueError):
        set_config_value(p, "risk.nonexistent", 1)


def test_no_comment_line_keeps_newline_and_next_line(tmp_path):
    p = _write(tmp_path, "risk:\n  require_uptrend: true\n  take1_pct: 6.0\n")
    set_config_value(p, "risk.require_uptrend", False, expected_current=True)
    assert p.read_text(encoding="utf-8") == "risk:\n  require_uptrend: false\n  take1_pct: 6.0\n"


def test_preserves_crlf_line_endings(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b"risk:\r\n  take1_pct: 6.0\r\n  stop_pct: 3.0\r\n")
    set_config_value(p, "risk.take1_pct", 6.5, expected_current=6.0)
    assert p.read_bytes() == b"risk:\r\n  take1_pct: 6.5\r\n  stop_pct: 3.0\r\n"
