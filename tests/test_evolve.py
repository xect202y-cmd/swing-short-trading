import json
import types

from swing_trader.review import evolve as EV
from swing_trader.review import proposals as P


def _cfg(tmp_path):
    return types.SimpleNamespace(
        state_dir=tmp_path,
        creds=types.SimpleNamespace(discord_webhook_url=None))


def _ab(verdict):
    rep = lambda e, s: types.SimpleNamespace(expectancy=e, sharpe=s)
    return types.SimpleNamespace(verdict=verdict, n_oos=143,
                                 base_oos=rep(0.62, 0.11), cand_oos=rep(0.70, 0.14))


def _review(suggestions):
    return ({"ok": True, "date": "2026-07-11", "suggestions": suggestions}, "evidence")


def test_evaluate_improve_creates_pending(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(EV.LR, "build_review", lambda c: _review([
        {"title": "익절상향", "insight": "i", "config_key": "risk.take1_pct",
         "current": 6.0, "suggested": 6.5}]))
    monkeypatch.setattr(EV.HN, "compare", lambda *a, **k: _ab("improve"))
    r = EV.evaluate(cfg, None, [], 500)
    props = P.load(tmp_path)
    assert r["ok"] and len(r["proposed"]) == 1
    assert len(props) == 1 and props[0]["config_key"] == "risk.take1_pct"
    assert props[0]["status"] == "pending" and props[0]["tier"] == "T1"


def test_evaluate_worse_learns_and_skips_on_rerun(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    sug = {"title": "t", "insight": "i", "config_key": "risk.default_stop_pct",
           "current": -3.0, "suggested": -3.5}
    monkeypatch.setattr(EV.LR, "build_review", lambda c: _review([sug]))
    monkeypatch.setattr(EV.HN, "compare", lambda *a, **k: _ab("worse"))
    EV.evaluate(cfg, None, [], 500)
    rules = json.loads((tmp_path / "learned_rules.json").read_text(encoding="utf-8"))
    assert any(k.startswith("reject:risk.default_stop_pct:") for k in rules)
    assert P.load(tmp_path) == []
    # 재실행: compare 를 improve 로 바꿔도 이미 기각 학습돼 재제안·재백테 안 함
    called = {"n": 0}
    def _cmp(*a, **k):
        called["n"] += 1
        return _ab("improve")
    monkeypatch.setattr(EV.HN, "compare", _cmp)
    EV.evaluate(cfg, None, [], 500)
    assert called["n"] == 0 and P.load(tmp_path) == []


def test_evaluate_t2_not_backtested(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    sug = {"title": "t", "insight": "i", "config_key": "risk.min_reward_risk",
           "current": 1.75, "suggested": 2.0}
    monkeypatch.setattr(EV.LR, "build_review", lambda c: _review([sug]))
    called = {"n": 0}
    monkeypatch.setattr(EV.HN, "compare",
                        lambda *a, **k: (called.__setitem__("n", called["n"] + 1), _ab("improve"))[1])
    r = EV.evaluate(cfg, None, [], 500)
    assert called["n"] == 0            # T2 는 백테 안 함
    assert P.load(tmp_path) == [] and len(r["t2"]) == 1


def test_evaluate_low_sample_returns_not_ok(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(EV.LR, "build_review",
                        lambda c: ({"ok": False, "reason": "청산 3건 미만"}, "ev"))
    r = EV.evaluate(cfg, None, [], 500)
    assert r["ok"] is False and "3건" in r["reason"]


def test_adopt_applies_config_and_versions(monkeypatch, tmp_path):
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text("risk:\n  take1_pct: 6.0   # 익절\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct",
                        "current": 6.0, "suggested": 6.5, "status": "pending"})
    # snapshot/load_config 는 무겁게 실제 cfg 필요 → 스텁
    monkeypatch.setattr(EV, "load_config", lambda p: "NEWCFG")
    monkeypatch.setattr(EV.LV, "snapshot", lambda c: {"risk.take1_pct": 6.5})
    r = EV.adopt(cfg, "A3", cfgfile)
    assert r["ok"] and r["version"] >= 1
    assert "take1_pct: 6.5" in cfgfile.read_text(encoding="utf-8")
    assert P.find(tmp_path, "A3")["status"] == "adopted"
    rules = json.loads((tmp_path / "learned_rules.json").read_text(encoding="utf-8"))
    assert any(k.startswith("accept:risk.take1_pct") for k in rules)


def test_adopt_unknown_id(tmp_path):
    r = EV.adopt(_cfg(tmp_path), "ZZ", tmp_path / "config.yaml")
    assert r["ok"] is False and "없음" in r["reason"]


def test_adopt_already_processed(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct",
                        "current": 6.0, "suggested": 6.5, "status": "adopted"})
    r = EV.adopt(cfg, "A3", tmp_path / "config.yaml")
    assert r["ok"] is False and "adopted" in r["reason"]


def test_reject_records_and_learns(tmp_path):
    cfg = _cfg(tmp_path)
    P.upsert(tmp_path, {"id": "B7", "config_key": "risk.take1_pct",
                        "current": 6.0, "suggested": 6.5, "status": "pending"})
    r = EV.reject(cfg, "B7")
    assert r["ok"] and P.find(tmp_path, "B7")["status"] == "rejected"
    rules = json.loads((tmp_path / "learned_rules.json").read_text(encoding="utf-8"))
    assert any(k.startswith("reject:risk.take1_pct") for k in rules)
