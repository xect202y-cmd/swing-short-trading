import pytest

from swing_trader.review import proposals as P


def test_classify_t1_t2():
    assert P.classify("risk.take1_pct") == "T1"
    assert P.classify("risk.min_reward_risk") == "T2"
    assert P.classify(None) == "T2"


def test_candidate_params_maps_to_override_kwarg():
    assert P.candidate_params("risk.take1_pct", 6.5) == {"take_pct": 6.5}
    assert P.candidate_params("risk.require_uptrend", False) == {"require_uptrend": False}
    assert P.candidate_params("risk.max_hold_days", 50) == {"max_hold": 50}


def test_direction():
    assert P.direction(6.0, 6.5) == "up"
    assert P.direction(-3.0, -3.5) == "down"
    assert P.direction(True, False) == "=false"


def test_proposal_id_deterministic():
    a = P.proposal_id("2026-07-11", "risk.take1_pct", 6.5)
    b = P.proposal_id("2026-07-11", "risk.take1_pct", 6.5)
    assert a == b and len(a) == 3 and a.isupper()


def test_store_roundtrip(tmp_path):
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct", "status": "pending"})
    assert P.find(tmp_path, "A3")["config_key"] == "risk.take1_pct"
    # upsert 는 같은 id 교체(중복 방지)
    P.upsert(tmp_path, {"id": "A3", "config_key": "risk.take1_pct", "status": "pending"})
    assert len(P.load(tmp_path)) == 1
    assert P.set_status(tmp_path, "A3", "adopted") is True
    assert P.find(tmp_path, "A3")["status"] == "adopted"
    assert P.set_status(tmp_path, "ZZ", "adopted") is False


def test_load_ignores_wrong_shape_json(tmp_path):
    (tmp_path / "pending_proposals.json").write_text("[1,2,3]", encoding="utf-8")
    assert P.load(tmp_path) == []
    (tmp_path / "pending_proposals.json").write_text('{"proposals": "oops"}', encoding="utf-8")
    assert P.load(tmp_path) == []


def test_candidate_params_rejects_t2():
    with pytest.raises(ValueError):
        P.candidate_params("risk.min_reward_risk", 2.0)


def test_direction_bool_true_and_unknown():
    assert P.direction(False, True) == "=true"
    assert P.direction(None, "x") == "?"
