"""거시 국면 — 거시 대시보드/국면 노트에서 VIX·금리·환율을 읽어 위험도 산정."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import RiskLevel

_NUM = r"(-?\d[\d,]*\.?\d*)"


def _find(text: str, *labels: str) -> float | None:
    for lab in labels:
        m = re.search(rf"{re.escape(lab)}[^0-9\-]*{_NUM}", text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


@dataclass
class MacroState:
    vix: float | None = None
    ust10y: float | None = None
    usdkrw: float | None = None
    risk: RiskLevel = RiskLevel.LOW
    notes: list[str] = field(default_factory=list)


def assess_macro(dashboard_md: str, regime_md: str, vix_caution: float = 20.0) -> MacroState:
    text = f"{dashboard_md}\n{regime_md}"
    st = MacroState(
        vix=_find(text, "VIX", "변동성지수"),
        ust10y=_find(text, "10년", "국채 10년", "DGS10"),
        usdkrw=_find(text, "원/달러", "원달러", "환율", "DEXKOUS"),
    )
    score = 0
    if st.vix is not None and st.vix >= vix_caution:
        score += 2
        st.notes.append(f"VIX {st.vix} ≥ {vix_caution} → 변동성 경계")
    if st.ust10y is not None and st.ust10y >= 4.5:
        score += 1
        st.notes.append(f"미 10년물 {st.ust10y}% 높음 → 성장/기술주 부담")
    if st.usdkrw is not None and st.usdkrw >= 1450:
        score += 1
        st.notes.append(f"원/달러 {st.usdkrw} 높음 → 외국인 수급 리스크")
    if not text.strip():
        st.notes.append("거시 노트 비어있음 — 보수적으로 중간 가정 안 함(낮음 유지)")

    st.risk = RiskLevel.HIGH if score >= 3 else RiskLevel.MEDIUM if score >= 1 else RiskLevel.LOW
    return st
