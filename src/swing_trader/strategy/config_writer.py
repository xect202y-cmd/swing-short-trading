"""config.yaml 단일 키 쓰기 — pyyaml 라운드트립은 주석을 파괴하므로 타깃 라인 편집.

대상은 leaf 이름이 config 전체에서 유일한 T1 키(risk.*)뿐. 정확히 1줄만 매칭돼야 한다.
"""
from __future__ import annotations

import re
from pathlib import Path


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def set_config_value(config_path: Path, dotted_key: str, new_value, expected_current=None) -> None:
    leaf = dotted_key.split(".")[-1]
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pat = re.compile(
        rf"^(?P<indent>\s*){re.escape(leaf)}:\s*(?P<val>[^#\n\r]*?)\s*"
        rf"(?P<comment>#.*?)?(?P<nl>\r?\n?)$"
    )
    hits = [(i, m) for i, line in enumerate(lines) if (m := pat.match(line))]
    if len(hits) != 1:
        raise ValueError(
            f"config 키 '{dotted_key}'(leaf={leaf}) 매칭 {len(hits)}줄 — 정확히 1줄이어야 함"
        )
    i, m = hits[0]
    cur = m.group("val").strip()
    if expected_current is not None and cur != _fmt(expected_current):
        raise ValueError(
            f"'{dotted_key}' 현재값 불일치: 파일 {cur!r} ≠ 기대 {_fmt(expected_current)!r}"
        )
    comment = m.group("comment") or ""
    tail = f"  {comment}" if comment else ""
    lines[i] = f"{m.group('indent')}{leaf}: {_fmt(new_value)}{tail}{m.group('nl')}"
    config_path.write_text("".join(lines), encoding="utf-8")
