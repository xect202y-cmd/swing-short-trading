"""Tests for check-done and notify-failover CLI subcommands."""
from datetime import datetime
from pathlib import Path

from swing_trader.cli import main as cli_main
from swing_trader.state import daily_marker as DM


def _seed_cfg(tmp_path, monkeypatch):
    # check-done은 cfg.state_dir만 필요 — load_config을 가벼운 더미로 패치
    class _Cfg:
        state_dir = tmp_path

        class creds:  # noqa
            discord_webhook_url = None

    monkeypatch.setattr("swing_trader.cli.load_config", lambda config_path=None: _Cfg)
    return _Cfg


def test_check_done_exit1_when_not_done(tmp_path, monkeypatch):
    _seed_cfg(tmp_path, monkeypatch)
    assert cli_main(["check-done", "--market", "kr"]) == 1


def test_check_done_exit0_when_done(tmp_path, monkeypatch):
    _seed_cfg(tmp_path, monkeypatch)
    DM.record_done(tmp_path, "kr", datetime.now(DM.KST))
    assert cli_main(["check-done", "--market", "kr"]) == 0


def test_notify_failover_sends_and_exits_0(tmp_path, monkeypatch):
    _seed_cfg(tmp_path, monkeypatch)
    sent = {}
    def _fake_notify(url, content):
        sent["url"] = url; sent["content"] = content; return True
    monkeypatch.setattr("swing_trader.notify.discord.notify", _fake_notify)
    rc = cli_main(["notify-failover", "--markets", "kr us"])
    assert rc == 0
    assert "KR" in sent["content"] and "US" in sent["content"]
