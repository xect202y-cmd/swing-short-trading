"""헤르메스 코칭 — 매일 스윙 매매일지를 읽고 친근한 멘토 톤으로 코멘트.

persona 정본(gpt-api/lib/persona.js)의 핵심 톤을 압축 반영:
친한 친구 같은 투자 파트너 · 추세추종 스윙 · 근거 중심 · 리스크 같이 짚기 ·
단기 일희일비 막고 장기목표(40세 50억) 상기 · '오늘 할 행동 하나'로 마무리.
키(OPENAI_API_KEY) 없으면 None → 코칭 생략(안전).
"""
from __future__ import annotations

from ..strategy.ai_judge import chat_text

_SYS = (
    "너는 이용수의 개인 투자 파트너 '헤르메스'다. 20년 경력 추세추종 스윙 트레이더이자, "
    "곁에서 함께 고민하는 친한 친구처럼 따뜻하고 친근하게 말한다. 단기 스윙 페이퍼 매매일지를 보고 코칭한다. "
    "원칙: ①근거 중심으로, 기회와 리스크를 늘 함께 짚는다 ②단기 수익률에 일희일비하면 장기목표(40세 50억)·"
    "분할/손절 원칙을 상기시켜 뇌동매매를 막는다 ③무조건 칭찬·동의 말고 반대 시나리오·위험도 짚어 균형을 잡는다 "
    "④추측 금지(일지에 적힌 사실만) ⑤마지막에 '오늘 수정할 로직:' 으로 1~3개를 구체적으로 제안한다"
    "(예: 진입근거 로그 추가, 차단 종목 사후추적, 총비중 60% 초과 시 신규매수 제한). 친근한 반말 톤, 6문장 이내."
)


def daily_coaching(cfg, *, positions: list[dict], activity: dict, metrics, compound) -> str | None:
    ctx = ["[오늘 스윙 페이퍼 매매일지]"]
    ctx.append(f"활동: 매수 {activity.get('bought',0)} · 매도 {activity.get('sold',0)} · 차단 {len(activity.get('blocked',[]))}")
    if positions:
        ctx.append("보유:")
        for p in positions:
            ctx.append(f"  - {p['mkt']} {p['name']} 매수 {p['avg']:,.0f}→현재 {p['cur']:,.0f} "
                       f"({p['ret']:+.1f}%) 보유 {p['days']}일")
    else:
        ctx.append("보유: 없음")
    if metrics:
        ctx.append(f"누적: 수익율 {metrics.return_pct}% · 승률 {metrics.win_rate}% · "
                   f"청산 {metrics.n_closed}건 · PF {metrics.profit_factor}")
    if compound and compound.get("annual_pct") is not None:
        ctx.append(f"복리추정(현재페이스): 연 {compound['annual_pct']}% (표본 {compound['days']}일·추정)")
    ctx.append("\n위 일지를 보고 헤르메스로서 코칭해줘.")
    return chat_text(cfg, _SYS, "\n".join(ctx))
