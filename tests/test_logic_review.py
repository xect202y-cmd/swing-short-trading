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
