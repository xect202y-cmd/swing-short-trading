"""종목명 → 티커/코드 매핑.

소스 3중:
  1) BUILTIN — 검증된 주요 종목(보유종목 + KOSPI 대형 + 미국 한글명).
  2) 볼트 수확 — 컴파일된 종목노트가 이미 보유한 코드를 (이름→코드)로 흡수.
  3) 캐시 — state/ticker_map.json.
한글명 종목은 코드가 있어야 yfinance(.KS)/pykrx 로 실데이터 조회 가능.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import StockNote

# 검증된 한국 대형주 (이름 → 6자리 코드). 사용자 보유 종목은 이번 세션 네이버 API로 확인.
BUILTIN_KR: dict[str, str] = {
    "삼성전자": "005930", "삼성전자우": "005935", "SK하이닉스": "000660", "NAVER": "035420",
    "네이버": "035420", "카카오": "035720", "현대차": "005380", "기아": "000270",
    "LG에너지솔루션": "373220", "삼성바이오로직스": "207940", "셀트리온": "068270",
    "POSCO홀딩스": "005490", "포스코홀딩스": "005490", "현대모비스": "012330",
    "삼성SDI": "006400", "LG화학": "051910", "LG전자": "066570", "KB금융": "105560",
    "신한지주": "055550", "하나금융지주": "086790", "삼성물산": "028260", "SK": "034730",
    "한화에어로스페이스": "012450", "두산에너빌리티": "034020", "HD현대중공업": "329180",
    "크래프톤": "259960", "하이브": "352820", "삼성생명": "032830", "메리츠금융지주": "138040",
    "고려아연": "010130", "한미반도체": "042700", "HMM": "011200", "포스코퓨처엠": "003670",
    "에코프로비엠": "247540", "에코프로": "086520", "유한양행": "000100", "LG이노텍": "011070",
    "효성중공업": "298040", "LS ELECTRIC": "010120", "LS일렉트릭": "010120",
    "현대로템": "064350", "한국항공우주": "047810", "LIG넥스원": "079550",
    "에이피알": "278470", "APR": "278470", "삼양식품": "003230", "에코프로머티": "450080",
    # KOSDAQ (코드 검증됨, yfinance .KQ)
    "HPSP": "403870", "리노공업": "058470", "펄어비스": "263750", "레인보우로보틱스": "277810",
    "클래시스": "214150", "엔켐": "348370", "셀트리온제약": "068760",
}
# 미국 주식 한글명 → 티커 (네이버/사용자 노트 표기 대응)
BUILTIN_US: dict[str, str] = {
    "엔비디아": "NVDA", "아마존": "AMZN", "알파벳": "GOOGL", "알파벳A": "GOOGL", "알파벳 A": "GOOGL",
    "애플": "AAPL", "마이크로소프트": "MSFT", "테슬라": "TSLA", "메타": "META", "넷플릭스": "NFLX",
    "마이크론": "MU", "브로드컴": "AVGO", "퀄컴": "QCOM", "인텔": "INTC", "팔란티어": "PLTR",
    "소파이": "SOFI", "테이크투": "TTWO", "테이크-투 인터랙티브 소프트웨어": "TTWO",
    "소파이 테크놀로지스": "SOFI", "코인베이스": "COIN", "마이크로스트래티지": "MSTR",
    "디즈니": "DIS", "스타벅스": "SBUX", "AMD": "AMD",
}
_KR_CODE_RE = re.compile(r"^\d{6}$")
_US_RE = re.compile(r"^[A-Z]{1,5}$")


class TickerMap:
    def __init__(self, mapping: dict[str, str]):
        self._map = mapping

    def resolve(self, name: str | None) -> str | None:
        if not name:
            return None
        key = name.strip()
        if key in self._map:
            return self._map[key]
        # 공백/접미사 정규화 재시도
        norm = key.replace(" ", "")
        for k, v in self._map.items():
            if k.replace(" ", "") == norm:
                return v
        return None

    def __len__(self) -> int:
        return len(self._map)


def build_ticker_map(notes: list[StockNote], state_dir: Path) -> TickerMap:
    mapping: dict[str, str] = {**BUILTIN_KR, **BUILTIN_US}
    # 볼트 수확: 코드를 이미 가진 노트(컴파일됨)의 이름→코드.
    for n in notes:
        if n.name and n.ticker and (_KR_CODE_RE.match(n.ticker) or _US_RE.match(n.ticker)):
            mapping.setdefault(n.name.strip(), n.ticker)
    # 캐시(증분 병합)
    cache = state_dir / "ticker_map.json"
    if cache.exists():
        try:
            for k, v in json.loads(cache.read_text(encoding="utf-8")).items():
                mapping.setdefault(k, v)
        except (OSError, json.JSONDecodeError):
            pass
    try:
        cache.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return TickerMap(mapping)
