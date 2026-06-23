"""종목 노트 파싱 — YAML frontmatter + 본문 텍스트 모두에서 값 추출.

형식이 일정하지 않으므로 다중 키 별칭 + 본문 정규식으로 best-effort 파싱한다.
필수값이 없으면 None 으로 두고 missing 에 사유를 기록한다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..models import StockNote

# 표준 필드 → 노트에서 쓰일 수 있는 키 별칭들
FIELD_ALIASES: dict[str, list[str]] = {
    "name": ["종목명", "이름", "name", "title", "제목"],
    "ticker": ["티커", "종목코드", "코드", "ticker", "symbol", "code"],
    "price": ["현재가", "종가", "price", "현재가격"],
    "target1": ["목표가1", "목표가", "target1", "1차목표"],
    "target2": ["목표가2", "target2", "2차목표"],
    "target_mean": ["평균목표가", "목표주가컨센서스", "컨센서스목표가", "target_mean"],
    "stop": ["손절가", "손절", "stop", "stoploss"],
    "buy_zone1": ["매수구간1", "매수가", "buy_zone1", "매수1"],
    "buy_zone2": ["매수구간2", "buy_zone2", "매수2"],
    "recommendable": ["추천가능여부", "추천가능", "recommendable"],
    "status": ["현재상태", "상태", "status"],
    "grade": ["타점등급", "등급", "grade"],
    "sector": ["섹터", "업종", "sector"],
    "market": ["시장", "마켓", "market"],
    "risk": ["리스크", "위험", "risk"],
    "memo": ["메모", "memo", "note", "비고"],
}
# 파일명/종목명에서 티커 유도용 패턴
_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_KR_CODE_RE = re.compile(r"^\d{6}$")
_NUM_FIELDS = {"price", "target1", "target2", "target_mean", "stop", "buy_zone1", "buy_zone2"}
_FM_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)
# 매수구간 "300,000 ~ 320,000" 같은 범위에서 첫/둘째 숫자 추출용
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, text[m.end():]


def _lookup(fm: dict[str, Any], body: str, aliases: list[str]) -> Any | None:
    # 1) frontmatter 키(대소문자/공백 무시 매칭)
    norm = {str(k).strip().lower(): v for k, v in fm.items()}
    for a in aliases:
        if a.strip().lower() in norm:
            val = norm[a.strip().lower()]
            if val not in (None, "", []):
                return val
    # 2) 본문 "키: 값" 또는 "키 값"(공백 구분) 패턴. 라인 시작 토큰만(예: '손절가는'은 미매칭).
    for a in aliases:
        pat = re.compile(rf"(?:^|\n)\s*[-*]?\s*{re.escape(a)}\s*(?:[:：]\s*|\s+)(.+)")
        bm = pat.search(body)
        if bm:
            return bm.group(1).strip()
    return None


def parse_stock_note(text: str, source_file: str | None = None) -> StockNote:
    fm, body = split_frontmatter(text)
    note = StockNote(source_file=source_file)
    for field_name, aliases in FIELD_ALIASES.items():
        raw = _lookup(fm, body, aliases)
        if raw is None:
            continue
        if field_name in _NUM_FIELDS:
            setattr(note, field_name, _to_number(raw))
        else:
            if isinstance(raw, list):                       # YAML 리스트(섹터 등) → "a/b"
                raw = "/".join(str(x) for x in raw if x)
            setattr(note, field_name, str(raw).strip() or None)

    # 파일명에서 종목명 보강 (예: "삼성전자.md")
    if not note.name and source_file:
        note.name = Path(source_file).stem

    # 티커 유도: 노트에 티커 필드가 없으면 파일명/종목명에서.
    #   미국=영문 티커(AMD), 한국=6자리 종목코드. 한글명/한국 영문명(APR=에이피알)은 코드맵이 해석.
    #   ★ 시장이 한국(KOSPI/KOSDAQ)이면 영문 종목명을 US 티커로 오인하지 않는다(다른 US 종목 오조회 방지).
    if not note.ticker:
        mkt = (note.market or "").upper()
        is_kr_market = ("한국" in (note.market or "")) or any(k in mkt for k in ("KOSPI", "KOSDAQ", "KRX", "KR"))
        # 시장:"기타"(중국/홍콩/일본 등 비미국 외국)는 영문 종목명을 US 티커로 오인 금지.
        # 예: BYD(비야디, 중국 전기차)를 NYSE 'BYD'(Boyd Gaming, 카지노)로 잘못 잡던 버그.
        is_other_market = "기타" in (note.market or "")
        for cand in (Path(source_file).stem if source_file else None, note.name):
            c = (cand or "").strip()
            if _KR_CODE_RE.match(c):
                note.ticker = c
                break
            if _US_TICKER_RE.match(c) and not is_kr_market and not is_other_market:
                note.ticker = c
                break

    # 필수값 점검 — 신호 생성 시 누락 사유 활용
    if not (note.name or source_file):
        note.missing.append("종목명/파일명 없음")
    if not note.ticker:
        note.missing.append("티커/종목코드 없음(시장데이터 조회 제한)")
    if note.stop is None and note.buy_zone1 is None and note.price is None:
        note.missing.append("손절/매수구간/현재가 모두 없음")
    return note


def parse_stock_file(path: Path) -> StockNote:
    return parse_stock_note(path.read_text(encoding="utf-8", errors="ignore"), source_file=path.name)
