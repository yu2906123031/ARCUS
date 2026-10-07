import json
from dataclasses import dataclass, fields
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import urlparse

@dataclass(frozen=True)
class Config:
    api_url: str = "https://api.arcus.xyz"
    ws_url: str = "wss://api.arcus.xyz/v1/ws"
    market: str = "BTC-USD"
    strategy: str = "mid"
    spread_bps: str = "2"
    bias_bps: str = "0"
    reprice_bps: str = "0.5"
    inventory_skew_bps: str = "0"
    adaptive_spread: bool = False
    batch_quote_cancels: bool = False
    max_spread_bps: str = "8"
    max_reprice_bps: str = "2"
    volatility_multiplier: str = "2"
    exit_fee_reserve_fraction: str = "0"
    order_equity_fraction: str = "0.1"
    max_position_equity_fraction: str = "0.5"
    stop_loss_equity_fraction: str = "0.02"
    leverage_cap: int = 3
    paper: bool = True
    paper_equity: str = "100"
    capital_cap: str | None = None
    paper_latency_ms: int = 150
    paper_exit_slippage_bps: str = "5"
    exit_slippage_bps: str = "20"
    disconnect_seconds: float = 10
    server_protection: bool = True
    protection_seconds: float = 180
    protection_refresh_seconds: float = 30
    max_clock_skew_ms: int = 5000
    clock_samples: int = 3
    clock_max_rtt_ms: int = 1000
    max_order_failures: int = 3
    quote_interval_seconds: float = 1
    account_refresh_seconds: float = 2
    confirmation_seconds: float = 8
    mutation_interval_seconds: float = 0.3
    log_dir: str = "mm_logs"

    def validate(self):
        if self.market not in ("BTC-USD", "ETH-USD") or self.strategy not in ("mid", "grid"):
            raise ValueError("only BTC-USD/ETH-USD and one of mid/grid are supported")
        for url, scheme in ((self.api_url, "https"), (self.ws_url, "wss")):
            if urlparse(url).scheme != scheme or not urlparse(url).hostname:
                raise ValueError("API URLs must use TLS")
        for name in ("spread_bps", "reprice_bps", "order_equity_fraction", "max_position_equity_fraction",
                     "stop_loss_equity_fraction", "paper_equity", "exit_slippage_bps", "paper_exit_slippage_bps"):
            value = D(getattr(self, name))
            if not value.is_finite() or value <= 0:
                raise ValueError("invalid " + name)
        if D(self.reprice_bps) > D(self.spread_bps):
            raise ValueError("reprice_bps must not exceed spread_bps")
        if type(self.batch_quote_cancels) is not bool: raise ValueError("invalid batch_quote_cancels")
        if type(self.adaptive_spread) is not bool: raise ValueError("invalid adaptive_spread")
        for name in ("inventory_skew_bps", "max_spread_bps", "max_reprice_bps", "volatility_multiplier", "exit_fee_reserve_fraction"):
            value=D(getattr(self,name))
            if not value.is_finite() or value<0: raise ValueError("invalid "+name)
        if D(self.inventory_skew_bps)>3: raise ValueError("inventory skew must be <=3 bps")
        if not D(self.spread_bps)<=D(self.max_spread_bps)<=50: raise ValueError("invalid maximum spread")
        if not D(self.reprice_bps)<=D(self.max_reprice_bps)<=D(self.max_spread_bps): raise ValueError("invalid maximum reprice")
        if not 0<D(self.volatility_multiplier)<=10: raise ValueError("invalid volatility multiplier")
        if D(self.exit_fee_reserve_fraction)>1: raise ValueError("invalid exit fee reserve fraction")
        if self.capital_cap is not None:
            cap=D(self.capital_cap)
            if not cap.is_finite() or cap<=0: raise ValueError("invalid capital_cap")
        bias = D(self.bias_bps)
        if not bias.is_finite() or abs(bias) > 3:
            raise ValueError("bias must be between -3 and +3 bps")
        if type(self.leverage_cap) is not int or not 1 <= self.leverage_cap <= 5:
            raise ValueError("this bot caps leverage at 5")
        if not 0 < D(self.order_equity_fraction) <= 1:
            raise ValueError("invalid order fraction")
        if not 0 < D(self.max_position_equity_fraction) <= self.leverage_cap:
            raise ValueError("position fraction exceeds leverage cap")
        if not 0 < D(self.stop_loss_equity_fraction) < 1:
            raise ValueError("invalid stop fraction")
        if D(self.exit_slippage_bps) > 500:
            raise ValueError("IOC slippage bound must be <=500 bps")
        for name in ("disconnect_seconds", "max_clock_skew_ms", "max_order_failures", "quote_interval_seconds",
                     "account_refresh_seconds", "confirmation_seconds", "mutation_interval_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not D(str(value)).is_finite() or value <= 0:
                raise ValueError("invalid " + name)
        if type(self.server_protection) is not bool: raise ValueError("invalid server_protection")
        for name in ("protection_seconds","protection_refresh_seconds"):
            value=getattr(self,name)
            if isinstance(value,bool) or not D(str(value)).is_finite(): raise ValueError("invalid "+name)
        if not 30<=self.protection_seconds<=300: raise ValueError("protection_seconds must be 30..300")
        if not 1<=self.protection_refresh_seconds<=self.protection_seconds/3:
            raise ValueError("protection refresh must leave at least two intervals of headroom")
        if type(self.clock_samples) is not int or not 2 <= self.clock_samples <= 5:
            raise ValueError("clock_samples must be 2..5")
        if type(self.clock_max_rtt_ms) is not int or not 100 <= self.clock_max_rtt_ms <= 2000:
            raise ValueError("clock_max_rtt_ms must be 100..2000")
        if type(self.paper) is not bool or type(self.paper_latency_ms) is not int or self.paper_latency_ms < 0:
            raise ValueError("invalid paper configuration")

def load(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    unknown = set(raw) - {f.name for f in fields(Config)}
    if unknown: raise ValueError("unknown config fields: " + ", ".join(sorted(unknown)))
    c = Config(**raw)
    c.validate()
    return c
