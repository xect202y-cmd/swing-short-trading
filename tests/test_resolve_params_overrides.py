from swing_trader.strategy import backtest as BT


class _Cfg:
    def get(self, *keys, default=None):
        return default


def test_resolve_params_accepts_new_overrides():
    p = BT._resolve_params(_Cfg(), max_hold=10, require_uptrend=True, min_tv_eok=50)
    assert p["max_hold"] == 10
    assert p["require_uptrend"] is True
    assert p["min_tv_eok"] == 50.0


def test_resolve_params_defaults_from_cfg_when_none():
    # 오버라이드 미지정이면 cfg 기본(_Cfg.get 이 default 반환)
    p = BT._resolve_params(_Cfg())
    assert p["max_hold"] == 20            # cfg.get default
    assert p["require_uptrend"] is False  # cfg.get default
