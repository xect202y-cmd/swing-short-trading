"""볼트 읽기 — 거시/이벤트/룰북/종목 노트. 파일이 없으면 빈 결과로 graceful 처리."""
from __future__ import annotations

import glob
from pathlib import Path

from ..config import Config
from ..models import StockNote
from .parser import parse_stock_file


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


class VaultReader:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def macro_dashboard(self) -> str:
        return _read(self.cfg.read_path("macro_dashboard"))

    def macro_regime(self) -> str:
        return _read(self.cfg.read_path("macro_regime"))

    def event_calendar(self) -> str:
        return _read(self.cfg.read_path("event_calendar"))

    def rulebook(self) -> str:
        return _read(self.cfg.read_path("rulebook"))

    def stock_note_paths(self) -> list[Path]:
        pattern = str(self.cfg.vault_root / self.cfg.stock_notes_glob())
        return sorted(Path(p) for p in glob.glob(pattern))

    def stock_notes(self, limit: int | None = None) -> list[StockNote]:
        paths = self.stock_note_paths()
        if limit:
            paths = paths[:limit]
        out: list[StockNote] = []
        for p in paths:
            try:
                out.append(parse_stock_file(p))
            except Exception as e:  # noqa: BLE001 — 한 노트 실패가 전체를 막지 않게
                n = StockNote(source_file=p.name)
                n.missing.append(f"파싱 실패: {e}")
                out.append(n)
        return out
