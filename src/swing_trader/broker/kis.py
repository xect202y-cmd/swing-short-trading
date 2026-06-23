"""KisBroker — 한국투자증권 KIS Developers REST 연동 구조.

- OAuth 토큰 발급/갱신, 현재가/잔고/주문 함수.
- 키가 없거나 LIVE 보호장치가 꺼져 있으면 실주문을 절대 실행하지 않는다(이중 보호).
- 자격증명은 절대 로깅/출력하지 않는다.
공식 도메인:
  실전 https://openapi.koreainvestment.com:9443
  모의 https://openapivts.koreainvestment.com:29443
"""
from __future__ import annotations

import logging
import time
import uuid

import requests

from ..config import Config
from ..models import Order, Position
from .base import Broker

log = logging.getLogger(__name__)

_DOMAINS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "vps": "https://openapivts.koreainvestment.com:29443",
}


class LiveTradingBlocked(RuntimeError):
    """LIVE 보호장치가 꺼진 상태에서 실주문 시도."""


class KisBroker(Broker):
    name = "kis"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.creds = cfg.creds
        self.safety = cfg.safety
        self.base = _DOMAINS.get(self.creds.kis_env, _DOMAINS["vps"])
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ── 보호장치 ──
    def _require_live(self) -> None:
        if not self.safety.live_allowed:
            raise LiveTradingBlocked(
                "실주문 차단 — PAPER_TRADING=false + LIVE_TRADING=true + "
                "I_UNDERSTAND_LIVE_RISK=true 가 모두 필요합니다."
            )
        if not self.creds.has_kis:
            raise LiveTradingBlocked("KIS 자격증명 미설정 — 실주문 불가.")

    # ── OAuth ──
    def _ensure_token(self) -> str:
        if not self.creds.has_kis:
            raise LiveTradingBlocked("KIS 자격증명 미설정.")
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        url = f"{self.base}/oauth2/tokenP"
        r = requests.post(url, json={
            "grant_type": "client_credentials",
            "appkey": self.creds.kis_app_key,
            "appsecret": self.creds.kis_app_secret,
        }, timeout=10)
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.time() + int(d.get("expires_in", 86400))
        return self._token

    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self.creds.kis_app_key or "",
            "appsecret": self.creds.kis_app_secret or "",
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }

    # ── 조회 ──
    def get_price(self, symbol: str) -> float | None:
        if not self.creds.has_kis:
            return None
        try:
            url = f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-price"
            r = requests.get(url, headers=self._headers("FHKST01010100"),
                             params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol.split(".")[0]},
                             timeout=10)
            r.raise_for_status()
            return float(r.json()["output"]["stck_prpr"])
        except Exception as e:  # noqa: BLE001
            log.warning("KIS 현재가 조회 실패 %s: %s", symbol, e)
            return None

    def get_cash_balance(self) -> float:
        # 실제 잔고조회 TR 연동 자리. 키 없으면 0.
        if not self.creds.has_kis:
            return 0.0
        log.info("KIS 잔고조회는 실 계정 연동 시 구현(inquire-balance TR).")
        return 0.0

    def get_positions(self) -> list[Position]:
        return []  # inquire-balance output1 매핑 자리

    # ── 주문 (실주문 — 이중 보호) ──
    def _order(self, symbol: str, side: str, quantity: int, price: float | None, order_type: str) -> Order:
        self._require_live()  # ← 여기서 안 막히면 실주문. 보호장치 통과 필수.
        tr_id = ("TTTC0802U" if side == "BUY" else "TTTC0801U") if self.creds.kis_env == "real" \
            else ("VTTC0802U" if side == "BUY" else "VTTC0801U")
        body = {
            "CANO": (self.creds.kis_account_no or "")[:8],
            "ACNT_PRDT_CD": (self.creds.kis_account_no or "")[8:10] or "01",
            "PDNO": symbol.split(".")[0],
            "ORD_DVSN": "00" if order_type == "limit" else "01",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(int(price)) if price else "0",
        }
        url = f"{self.base}/uapi/domestic-stock/v1/trading/order-cash"
        r = requests.post(url, headers=self._headers(tr_id), json=body, timeout=10)
        oid = "K-" + uuid.uuid4().hex[:8]
        try:
            r.raise_for_status()
            out = r.json()
            status = "submitted" if out.get("rt_cd") == "0" else "rejected"
            oid = out.get("output", {}).get("ODNO", oid)
            return Order(oid, symbol, side, quantity, price or 0.0, order_type, status=status,
                         note=out.get("msg1", "")[:60])
        except Exception as e:  # noqa: BLE001
            return Order(oid, symbol, side, quantity, price or 0.0, order_type, status="rejected",
                         note=f"주문 실패: {e}")

    def place_buy_order(self, symbol, quantity, price=None, order_type="limit") -> Order:
        return self._order(symbol, "BUY", quantity, price, order_type)

    def place_sell_order(self, symbol, quantity, price=None, order_type="limit") -> Order:
        return self._order(symbol, "SELL", quantity, price, order_type)

    def get_order_status(self, order_id: str) -> str:
        return "unknown"  # inquire-ccnl TR 연동 자리
