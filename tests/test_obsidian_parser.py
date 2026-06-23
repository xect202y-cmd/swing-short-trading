"""옵시디언 종목 노트 파싱 — frontmatter + 본문, 숫자/범위, 누락 처리."""
from swing_trader.obsidian.parser import parse_stock_note

FM_NOTE = """---
종목명: 삼성전자
티커: "005930"
현재가: 354,000
목표가1: 457917
손절가: 340,000
매수구간1: 350,000
타점등급: A
섹터: 반도체
---
# 삼성전자
메모: 추세 양호. 눌림목 대기.
"""

BODY_NOTE = """# SK하이닉스
티커: 000660
현재가: 2,755,000
목표가1: 2,835,000원
손절가 2,600,000
리스크: 거래정지 이슈 없음
"""


def test_parse_frontmatter_fields():
    n = parse_stock_note(FM_NOTE, source_file="삼성전자.md")
    assert n.name == "삼성전자"
    assert n.ticker == "005930"
    assert n.price == 354000.0
    assert n.target1 == 457917.0
    assert n.stop == 340000.0
    assert n.buy_zone1 == 350000.0
    assert n.grade == "A"
    assert n.sector == "반도체"
    assert n.missing == []


def test_parse_body_patterns():
    n = parse_stock_note(BODY_NOTE, source_file="SK하이닉스.md")
    assert n.ticker == "000660"
    assert n.price == 2755000.0
    assert n.target1 == 2835000.0
    assert n.stop == 2600000.0


def test_missing_fields_recorded():
    n = parse_stock_note("# 무명종목\n내용 없음", source_file="무명종목.md")
    assert n.name == "무명종목"            # 파일명에서 보강
    assert n.ticker is None
    assert any("티커" in m for m in n.missing)


def test_filename_fallback_for_name():
    n = parse_stock_note("티커: 123456\n", source_file="한화에어로스페이스.md")
    assert n.name == "한화에어로스페이스"
