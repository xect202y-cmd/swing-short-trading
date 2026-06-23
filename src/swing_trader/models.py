"""도메인 모델 — 종목 노트, 기술적 스냅샷, 점수, 신호, 주문, 포지션."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SignalKind(StrEnum):
    BUY_WATCH = "BUY_WATCH"
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    BLOCKED = "BLOCKED"


class RiskLevel(StrEnum):
    LOW = "낮음"
    MEDIUM = "중간"
    HIGH = "높음"


@dataclass
class StockNote:
    """옵시디언 종목 노트에서 파싱한 값(없으면 None)."""
    name: str | None = None
    ticker: str | None = None
    price: float | None = None
    target1: float | None = None
    target2: float | None = None
    target_mean: float | None = None
    stop: float | None = None
    buy_zone1: float | None = None
    buy_zone2: float | None = None
    recommendable: str | None = None
    status: str | None = None
    grade: str | None = None
    sector: str | None = None
    market: str | None = None       # 한국 | 미국 (노트 '시장' 필드)
    risk: str | None = None
    memo: str | None = None
    source_file: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or (self.source_file or "?")


@dataclass
class TechnicalSnapshot:
    """기술적 지표 스냅샷(시장 OHLCV 기반)."""
    ticker: str
    price: float
    ma: dict[int, float] = field(default_factory=dict)        # {5:.., 20:.., 60:..}
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    vol_avg: dict[int, float] = field(default_factory=dict)   # {5:.., 20:..}
    vol_today: float | None = None
    prev_high: float | None = None
    high_20: float | None = None
    low_20: float | None = None
    trading_value_eok: float | None = None                 # 거래대금(억)
    atr: float | None = None                               # ATR(14) 절대값
    atr_pct: float | None = None                           # ATR / 현재가 * 100
    gap_pct: float | None = None                           # 당일 시가 갭(전일종가 대비 %)
    today_open: float | None = None

    @property
    def near_ma20(self) -> bool:
        m = self.ma.get(20)
        return m is not None and abs(self.price - m) / m <= 0.03

    @property
    def above_ma5(self) -> bool:
        m = self.ma.get(5)
        return m is not None and self.price >= m

    @property
    def above_ma20(self) -> bool:
        m = self.ma.get(20)
        return m is not None and self.price >= m

    @property
    def ma_aligned(self) -> bool:
        a, b, c = self.ma.get(5), self.ma.get(20), self.ma.get(60)
        return bool(a and b and c and a >= b >= c)

    @property
    def breakout_prev_high(self) -> bool:
        return self.prev_high is not None and self.price > self.prev_high

    @property
    def volume_surge(self) -> float | None:
        v5, v20 = self.vol_avg.get(5), self.vol_avg.get(20)
        if v5 and v20 and v20 > 0:
            return v5 / v20
        return None


@dataclass
class ScoreItem:
    name: str
    score: float
    max_score: float
    reason: str = ""


@dataclass
class ScoreBreakdown:
    items: list[ScoreItem] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(i.score for i in self.items), 1)

    def add(self, name: str, score: float, max_score: float, reason: str = "") -> None:
        self.items.append(ScoreItem(name, round(score, 1), max_score, reason))

    def as_lines(self) -> list[str]:
        return [f"- {i.name}: {i.score:.0f}/{i.max_score:.0f} — {i.reason}" for i in self.items]


@dataclass
class TradePlan:
    """진입 전 반드시 계산되는 매매 플랜. 손절가 없는 신호는 금지."""
    entry: float
    stop: float
    target1: float
    target2: float | None = None

    @property
    def risk_pct(self) -> float:
        return (self.stop - self.entry) / self.entry * 100

    @property
    def reward1_pct(self) -> float:
        return (self.target1 - self.entry) / self.entry * 100

    @property
    def reward_risk(self) -> float | None:
        r = abs(self.risk_pct)
        return round(self.reward1_pct / r, 2) if r > 0 else None


@dataclass
class Signal:
    ticker: str
    name: str
    kind: SignalKind
    score: float
    price: float
    plan: TradePlan | None = None
    macro_risk: RiskLevel = RiskLevel.LOW
    event_risk: RiskLevel = RiskLevel.LOW
    breakdown: ScoreBreakdown | None = None
    reasons: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)   # 부합한 매매 기법(playbook)
    ai_verdict: str | None = None                       # AI 최종판단(매수/관망/회피)
    ai_reason: str | None = None
    ai_confidence: int | None = None
    rationale: dict = field(default_factory=dict)        # 진입 근거(진입유형·가격/거래량/추세/시장/제외)
    target_methods: dict = field(default_factory=dict)   # 목표/손절 후보(고정/ATR/지지저항)
    target_method_used: str | None = None               # 실제 채택한 방식
    rank: int | None = None                             # 오늘 후보 순위
    atr_pct: float | None = None
    sector: str | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Order:
    order_id: str
    ticker: str
    side: str           # BUY | SELL
    quantity: int
    price: float        # 지정가(또는 체결가)
    order_type: str = "limit"
    status: str = "submitted"   # submitted | filled | rejected | canceled
    filled_price: float | None = None
    fee: float = 0.0
    slippage: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    note: str = ""


@dataclass
class Position:
    ticker: str
    name: str
    quantity: int
    avg_price: float
    stop: float
    target1: float
    target2: float | None = None
    opened_at: datetime = field(default_factory=datetime.now)
    bars_held: int = 0
    entry_score: float | None = None     # 진입 시 스윙 점수(점수-결과 상관 분석용)
    entry_date: str | None = None        # 진입 거래일(YYYY-MM-DD)
    entry_reasons: list[str] = field(default_factory=list)
    partial_done: bool = False           # 1차 익절(절반) 완료 → 잔량은 트레일링으로 추세 보유
    high_water: float = 0.0              # 진입 후 최고가(트레일링 스탑 기준)
    sector: str | None = None            # 진입 섹터(포트폴리오 섹터비중 산출용)

    def unrealized_pct(self, last: float) -> float:
        return (last - self.avg_price) / self.avg_price * 100

    def unrealized_pnl(self, last: float) -> float:
        return (last - self.avg_price) * self.quantity
