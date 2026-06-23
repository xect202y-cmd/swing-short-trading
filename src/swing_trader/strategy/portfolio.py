"""포트폴리오 노출(비중) 계산 + 한도 점검 — 총비중/종목비중/섹터비중/하루 신규수.

비중 기준값은 시드(고정 분모). 한도 초과 시 신규매수를 차단한다.
"""
from __future__ import annotations


class Exposure:
    def __init__(self, seed: float, positions, price_fn):
        self.seed = max(seed, 1.0)
        self.holdings = 0.0
        self.per_stock: dict[str, float] = {}
        self.per_sector: dict[str, float] = {}
        for p in positions:
            cur = price_fn(p.ticker) or p.avg_price
            val = cur * p.quantity
            self.holdings += val
            self.per_stock[p.ticker] = self.per_stock.get(p.ticker, 0.0) + val
            sec = p.sector or "기타"
            self.per_sector[sec] = self.per_sector.get(sec, 0.0) + val

    def _pct(self, v: float) -> float:
        return round(v / self.seed * 100, 1)

    def report(self, limits: dict) -> dict:
        return {
            "total_pct": self._pct(self.holdings),
            "max_total": limits["total"],
            "per_sector_pct": {s: self._pct(v) for s, v in sorted(self.per_sector.items(), key=lambda x: -x[1])},
            "max_sector": limits["sector"],
            "max_per_stock": limits["stock"],
            "holdings": round(self.holdings, 0),
        }

    def can_add(self, ticker: str, sector: str | None, value: float, limits: dict) -> tuple[bool, list[str]]:
        sec = sector or "기타"
        reasons = []
        if self._pct(self.holdings + value) > limits["total"]:
            reasons.append(f"총 투자비중 {self._pct(self.holdings + value)}% > {limits['total']}% 초과")
        if self._pct(self.per_stock.get(ticker, 0.0) + value) > limits["stock"]:
            reasons.append(f"종목 비중 {self._pct(self.per_stock.get(ticker, 0.0) + value)}% > {limits['stock']}% 초과")
        if self._pct(self.per_sector.get(sec, 0.0) + value) > limits["sector"]:
            reasons.append(f"섹터({sec}) 비중 {self._pct(self.per_sector.get(sec, 0.0) + value)}% > {limits['sector']}% 초과")
        return len(reasons) == 0, reasons

    def add(self, ticker: str, sector: str | None, value: float) -> None:
        sec = sector or "기타"
        self.holdings += value
        self.per_stock[ticker] = self.per_stock.get(ticker, 0.0) + value
        self.per_sector[sec] = self.per_sector.get(sec, 0.0) + value


def limits_from_cfg(cfg) -> dict:
    cap = cfg.get("capital", default={})
    return {
        "total": float(cap.get("max_total_exposure_pct", 60)),
        "stock": float(cap.get("max_per_stock_pct", 25)),
        "sector": float(cap.get("max_sector_pct", 35)),
        "max_new": int(cap.get("max_new_per_day", 3)),
    }
