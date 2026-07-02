"""설정 로딩: config.yaml + .env 병합, 경로 해석, 안전 플래그.

민감정보(API 키)는 .env 에서만 읽고 절대 로깅/출력하지 않는다(redact 헬퍼 제공).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PKG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_ROOT.parents[1]  # .../swing-short-trading


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def redact(value: str | None) -> str:
    """자격증명 안전 출력. 절대 원문 노출 금지."""
    if not value:
        return "(미설정)"
    return "[REDACTED]"


@dataclass(frozen=True)
class Safety:
    paper_trading: bool = True
    live_trading: bool = False
    understand_live_risk: bool = False

    @property
    def live_allowed(self) -> bool:
        """실전 주문 허용 여부 — 이중(삼중) 보호."""
        return (not self.paper_trading) and self.live_trading and self.understand_live_risk


@dataclass(frozen=True)
class Credentials:
    kis_app_key: str | None = None
    kis_app_secret: str | None = None
    kis_account_no: str | None = None
    kis_env: str = "vps"  # vps(모의도메인) | real
    discord_webhook_url: str | None = None   # 스윙 알림 전용(Hermes와 분리 권장)
    scalp_webhook: str | None = None         # 단타 페이퍼 전용(스윙과 시각 분리)
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    @property
    def has_kis(self) -> bool:
        return bool(self.kis_app_key and self.kis_app_secret and self.kis_account_no)


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    vault_root: Path
    safety: Safety
    creds: Credentials
    state_dir: Path

    # ── 편의 접근자 ──
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def read_path(self, key: str) -> Path:
        rel = self.get("paths", "read", key, default="")
        return self.vault_root / rel

    def stock_notes_glob(self) -> str:
        return str(self.get("paths", "read", "stock_notes_glob", default="02_투자_유튜브/종목/*.md"))

    def write_dir(self, key: str) -> Path:
        rel = self.get("paths", "write", key, default="04_Trading")
        return self.vault_root / rel


def _load_env() -> None:
    """우선순위: 프로세스 env > 프로젝트 .env > C:\\hermes\\config\\.env.swing."""
    for p in (Path(r"C:\hermes\config\.env.swing"), PROJECT_ROOT / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


@cache
def load_config(config_path: str | None = None) -> Config:
    _load_env()
    cfg_file = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
    raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    raw = raw or {}

    vault_root = Path(
        os.getenv("OBSIDIAN_VAULT_ROOT")
        or raw.get("paths", {}).get("vault_root")
        or r"C:\Users\xect2\ObsidianVault\이용수_Wiki"
    )

    safety = Safety(
        paper_trading=_truthy(os.getenv("PAPER_TRADING", "true")),
        live_trading=_truthy(os.getenv("LIVE_TRADING", "false")),
        understand_live_risk=_truthy(os.getenv("I_UNDERSTAND_LIVE_RISK", "false")),
    )
    creds = Credentials(
        kis_app_key=os.getenv("KIS_APP_KEY") or None,
        kis_app_secret=os.getenv("KIS_APP_SECRET") or None,
        kis_account_no=os.getenv("KIS_ACCOUNT_NO") or None,
        kis_env=os.getenv("KIS_ENV", "vps"),
        discord_webhook_url=os.getenv("SWING_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or None,
        scalp_webhook=os.getenv("SCALP_DISCORD_WEBHOOK_URL")
                      or os.getenv("SWING_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )
    state_dir = PROJECT_ROOT / str(raw.get("paths", {}).get("state_dir", "state"))
    state_dir.mkdir(parents=True, exist_ok=True)

    return Config(raw=raw, vault_root=vault_root, safety=safety, creds=creds, state_dir=state_dir)
