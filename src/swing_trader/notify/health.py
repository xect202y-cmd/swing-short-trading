"""데이터 수집 건전성 점검 + 실패 시 디스코드 ⚠️ 경고.

스윙 일일 런 / 주간 백테스트에서 시장 데이터 수집이 실패하면(수집 대상 0건·전부
synthetic 폴백) 조용히 빈/가짜 리포트를 쌓지 않고 Swing 채널로 경고를 보낸다.
exception(예외)으로 단계가 통째로 죽는 경우는 호출부에서 try/except 로 잡아 alert() 호출.
"""
from __future__ import annotations

from dataclasses import dataclass

_REAL = ("pykrx", "yfinance")


@dataclass
class DataHealth:
    ok: bool
    n_total: int        # 티커가 있어 수집을 시도한 종목 수
    n_real: int         # 실데이터(pykrx/yfinance)로 수집된 종목 수
    reason: str = ""    # 실패 사유(ok=False 일 때만)


def assess(sources) -> DataHealth:
    """get_ohlcv 가 돌려준 source 목록으로 수집 건전성 판정.

    - 수집 대상 0건            → 실패(종목노트/티커 해석 실패로 추정)
    - 실데이터 0건(전부 synthetic) → 실패(pykrx/yfinance 수집 실패로 추정)
    - 실데이터 1건 이상         → 정상
    """
    srcs = [s for s in sources if s]
    n_total = len(srcs)
    n_real = sum(1 for s in srcs if s in _REAL)
    if n_total == 0:
        return DataHealth(False, 0, 0, "수집 대상 0건 — 종목노트/티커 해석 실패로 추정")
    if n_real == 0:
        return DataHealth(
            False, n_total, 0,
            f"실데이터 0건 — {n_total}종목 전부 synthetic 폴백(pykrx/yfinance 수집 실패)",
        )
    return DataHealth(True, n_total, n_real)


def alert(webhook_url, stage: str, detail: str) -> bool:
    """데이터 수집 실패를 Swing 디스코드로 경고. 웹훅 없으면 콘솔 출력."""
    from .discord import notify
    msg = (
        f"⚠️ 스윙 데이터 수집 실패 — **{stage}**\n"
        f"{detail}\n"
        "→ 리포트가 비거나 가짜(synthetic) 데이터일 수 있어. 노트북/네트워크/데이터소스 점검 요망."
    )
    return notify(webhook_url, msg)
