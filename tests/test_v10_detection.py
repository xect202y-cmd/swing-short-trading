"""v10 신고가 거감짜름 — 검출 로직 단위테스트."""
import numpy as np
import pandas as pd

from swing_trader.config import load_config


def test_v10_config_defaults():
    cfg = load_config()
    assert cfg.get("v10", "high_n") == 252
    assert cfg.get("v10", "vol_x") == 2.0
    assert cfg.get("v10", "window") == 3
    assert cfg.get("v10", "vol_dry") == 0.7
    assert cfg.get("v10", "supply_days") == 3
    assert cfg.get("v10", "supply_required") is True
    assert cfg.get("v10", "regime_gate") is True
    assert cfg.get("v10", "regime_ma") == 50
    assert cfg.get("v10", "min_tv_eok") == 50
