import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from swing_trader.state import daily_marker as DM


def test_is_done_false_when_no_file(tmp_path: Path):
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is False


def test_record_then_is_done(tmp_path: Path):
    now = datetime(2026, 6, 29, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "kr", now)
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is True
    assert DM.is_done(tmp_path, "us", date(2026, 6, 29)) is False


def test_record_all_records_both_markets(tmp_path: Path):
    now = datetime(2026, 6, 29, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "all", now)
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is True
    assert DM.is_done(tmp_path, "us", date(2026, 6, 29)) is True


def test_prune_drops_keys_older_than_7_days(tmp_path: Path):
    old = datetime(2026, 6, 1, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "kr", old)
    new = datetime(2026, 6, 29, 9, 5, tzinfo=DM.KST)
    DM.record_done(tmp_path, "kr", new)
    data = json.loads((tmp_path / "daily_done.json").read_text(encoding="utf-8"))
    assert "2026-06-01" not in data
    assert "2026-06-29" in data


def test_corrupt_file_is_treated_as_empty(tmp_path: Path):
    (tmp_path / "daily_done.json").write_text("{not json", encoding="utf-8")
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is False


def test_nondict_json_is_treated_as_empty(tmp_path):
    (tmp_path / "daily_done.json").write_text("[1,2,3]", encoding="utf-8")
    assert DM.is_done(tmp_path, "kr", date(2026, 6, 29)) is False


def test_record_done_writes_iso_kst_timestamp(tmp_path):
    from datetime import datetime
    now = datetime(2026, 6, 29, 6, 0, tzinfo=DM.KST)
    DM.record_done(tmp_path, "us", now)
    data = json.loads((tmp_path / "daily_done.json").read_text(encoding="utf-8"))
    assert data["2026-06-29"]["us"].startswith("2026-06-29T06:00")
    assert "+09:00" in data["2026-06-29"]["us"]
