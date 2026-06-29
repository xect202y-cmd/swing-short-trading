import json
import types
from swing_trader.strategy import ai_judge


class _Cfg:
    class creds:
        openai_api_key = "sk-test"
        openai_model = "gpt-test"


def _resp(content):
    class R:
        ok = True
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": content}}]}
    return R()


def test_chat_json_parses_object(monkeypatch):
    captured = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return _resp('{"suggestions": [], "next_action": "x"}')
    monkeypatch.setattr(ai_judge.requests, "post", fake_post)
    out = ai_judge.chat_json(_Cfg, "sys", "user")
    assert out == {"suggestions": [], "next_action": "x"}
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_chat_json_returns_none_on_bad_json(monkeypatch):
    monkeypatch.setattr(ai_judge.requests, "post", lambda *a, **k: _resp("not json"))
    assert ai_judge.chat_json(_Cfg, "sys", "user") is None


def test_chat_json_none_without_key(monkeypatch):
    class NoKey:
        class creds:
            openai_api_key = ""
            openai_model = "m"
    assert ai_judge.chat_json(NoKey, "s", "u") is None


# ── Task 2: logic_reviewer 구조화 출력 ──────────────────────────────────────
from swing_trader.review import logic_reviewer as LR


def test_render_md_ok_includes_sections():
    state = {
        "ok": True, "date": "2026-06-26", "n_closed": 7,
        "headline": {"win_rate": 42.9, "profit_factor": 1.47, "return_pct": 1.52},
        "suggestions": [
            {"title": "승률 낮음", "insight": "70~79점 승률 43%.",
             "config_key": "scoring.thresholds.strong", "current": 80, "suggested": 75},
        ],
        "next_action": "진입문턱 80→75 후 A/B 검증",
    }
    md = LR.render_md(state, "[성과지표]\n- 청산 7건")
    assert "AI 로직 진단" in md
    assert "승률 낮음" in md
    assert "scoring.thresholds.strong" in md
    assert "80" in md and "75" in md
    assert "다음 액션" in md
    assert "<details>" in md  # 근거 데이터 접기


def test_render_md_low_sample():
    state = {"ok": False, "date": "2026-06-26", "n_closed": 1,
             "reason": "청산 3건 미만 — 데이터 축적 중"}
    md = LR.render_md(state, "[성과지표]\n- 청산 1건")
    assert "데이터" in md and "AI 로직 진단" in md


def test_build_review_low_sample(monkeypatch, tmp_path):
    # n_closed<3 → ok:False, LLM 미호출
    monkeypatch.setattr(LR, "_evidence_text",
                        lambda cfg: ("[성과지표]\n- 청산 1건",
                                     types.SimpleNamespace(n_closed=1, win_rate=0,
                                                           profit_factor=None, return_pct=0),
                                     {}))
    called = {"n": 0}
    monkeypatch.setattr(LR, "chat_json", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    state, _ = LR.build_review(_Cfg)
    assert state["ok"] is False and called["n"] == 0


def test_build_review_ok(monkeypatch):
    m = types.SimpleNamespace(n_closed=7, win_rate=42.9, profit_factor=1.47, return_pct=1.52)
    monkeypatch.setattr(LR, "_evidence_text", lambda cfg: ("[성과지표]\n- 청산 7건", m, {}))
    monkeypatch.setattr(LR, "chat_json", lambda *a, **k: {
        "suggestions": [{"title": "t", "insight": "i", "config_key": "k", "current": 1, "suggested": 2}],
        "next_action": "do x"})
    state, _ = LR.build_review(_Cfg)
    assert state["ok"] is True
    assert state["headline"] == {"win_rate": 42.9, "profit_factor": 1.47, "return_pct": 1.52}
    assert state["suggestions"][0]["title"] == "t"
    assert state["next_action"] == "do x"


def test_build_review_ai_none(monkeypatch):
    m = types.SimpleNamespace(n_closed=7, win_rate=42.9, profit_factor=1.47, return_pct=1.52)
    monkeypatch.setattr(LR, "_evidence_text", lambda cfg: ("ev", m, {}))
    monkeypatch.setattr(LR, "chat_json", lambda *a, **k: None)
    state, _ = LR.build_review(_Cfg)
    assert state["ok"] is False
