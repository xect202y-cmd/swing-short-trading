"""AI 최종 판단(선택) — 네 매매기법(playbook)을 렌즈로 상위 후보를 LLM이 검수.

데이터/판단 레이어 분리: 퀀트 점수·기법 매칭(사실/룰)이 후보를 올리면,
AI는 그 후보가 '네 기법 관점에서' 단기 스윙으로 적합한지 판단(매수/관망/회피)한다.
키(OPENAI_API_KEY) 없으면 None 반환 → 룰 기반만으로 동작(안전 폴백).
OpenAI 호환 엔드포인트(OPENAI_BASE_URL) 지원.
"""
from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger(__name__)

_SYS = (
    "너는 1~5일 단기 스윙 트레이딩 베테랑이다. 주어진 종목의 실데이터와 "
    "사용자의 매매기법(playbook)을 근거로, 지금 단기 스윙 매수로 적합한지 판단한다. "
    "추측 금지·데이터에 근거. 반드시 JSON만 출력: "
    '{"verdict":"매수|관망|회피","reason":"한 문장(한국어)","confidence":0-100}'
)


def chat_text(cfg, system: str, user: str) -> str | None:
    """범용 LLM 텍스트 호출(코칭 등). 키 없으면 None. gpt-5.5 등 temperature 미지원 대응으로 미지정."""
    key = cfg.creds.openai_api_key
    if not key:
        return None
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": cfg.creds.openai_model,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=30,
        )
        if not r.ok:
            log.warning("LLM 텍스트 응답 %s: %s", r.status_code, r.text[:160])
            return None
        return r.json()["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("LLM 텍스트 실패: %s", e)
        return None


def _prompt(sig, note, tech, methods: list[dict]) -> str:
    lines = [
        f"종목: {note.display_name} ({sig.ticker}) · 섹터 {note.sector or '-'}",
        f"현재가 {tech.price:,.0f} / MA5 {tech.ma.get(5):,.0f} / MA20 {tech.ma.get(20):,.0f} / MA60 {tech.ma.get(60):,.0f}",
        f"RSI {tech.rsi} · 거래량/20일평균 {round(tech.vol_today/max(tech.vol_avg.get(20,1),1),2)}배 · 거래대금 {tech.trading_value_eok}억",
        f"퀀트 점수 {sig.score:.0f} · 목표 {sig.plan.target1:,.0f}/손절 {sig.plan.stop:,.0f}" if sig.plan else f"퀀트 점수 {sig.score:.0f}",
    ]
    if methods:
        lines.append("부합 기법(playbook):")
        for m in methods:
            cond = "; ".join(m.get("진입조건", [])[:3])
            lines.append(f"  - {m.get('기법명')}: {cond}")
    lines.append("이 종목을 지금 단기 스윙 매수해도 좋은가?")
    return "\n".join(lines)


def judge(cfg, sig, note, tech, methods: list[dict]) -> dict | None:
    """단일 후보 AI 판단. (key 없으면 None)"""
    key = cfg.creds.openai_api_key
    if not key:
        return None
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = cfg.creds.openai_model
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": _SYS},
                             {"role": "user", "content": _prompt(sig, note, tech, methods)}],
                # temperature 미지정(기본=1) — gpt-5.5 등 일부 모델은 커스텀 temperature 미지원.
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        if not r.ok:
            log.warning("AI 판단 응답 %s: %s", r.status_code, r.text[:160])
            return None
        content = r.json()["choices"][0]["message"]["content"]
        obj = json.loads(content)
        v = str(obj.get("verdict", "")).strip()
        if v not in ("매수", "관망", "회피"):
            return None
        return {"verdict": v, "reason": str(obj.get("reason", ""))[:200],
                "confidence": int(obj.get("confidence", 0))}
    except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as e:
        log.warning("AI 판단 실패(%s): %s", sig.ticker, e)
        return None


def chat_json(cfg, system: str, user: str) -> dict | None:
    """범용 LLM JSON 호출(response_format=json_object). 키 없음/실패/파싱오류 시 None."""
    key = cfg.creds.openai_api_key
    if not key:
        return None
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": cfg.creds.openai_model,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "response_format": {"type": "json_object"}},
            timeout=30,
        )
        if not r.ok:
            log.warning("LLM JSON 응답 %s: %s", r.status_code, r.text[:160])
            return None
        return json.loads(r.json()["choices"][0]["message"]["content"])
    except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as e:
        log.warning("LLM JSON 실패: %s", e)
        return None
