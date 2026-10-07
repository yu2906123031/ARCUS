import json
from collections import deque
import time
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
BPS = D(10000)

def number(value):
    n = D(str(value))
    if not n.is_finite(): raise ValueError("non-finite exchange number")
    return n

def fmt(value): return format(value, "f")

@dataclass
class Market:
    id: int
    tick: D
    step: D
    min_size: D
    min_notional: D
    max_size: D
    tiers: list

    @classmethod
    def parse(cls, row):
        if row["status"] != "ONLINE" or row["type"] != "PERPETUAL":
            raise ValueError("market is not an online perpetual")
        vals = [number(row[k]) for k in ("tickSize", "stepSize", "minOrderSize", "minOrderNotional", "maxOrderSize")]
        if any(x <= 0 for x in vals): raise ValueError("invalid market metadata")
        return cls(int(row["marketId"]), *vals, row.get("tickTiers", []))

    def price(self, price, side):
        # Signing always divides by the top-level tick, not the tier tick.
        # https://docs.arcus.xyz/api-reference/authentication
        rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
        for _ in range(3):
            tick = self.tick
            for tier in self.tiers:
                upper = tier.get("upToPrice")
                if upper is None or price < number(upper):
                    tick = number(tier["tick"])
                    break
            new = (price / tick).to_integral_value(rounding=rounding) * tick
            if new == price: return new
            price = new
        return price

    def quantity(self, qty):
        return (max(D(0), min(qty, self.max_size)) / self.step).to_integral_value(rounding=ROUND_FLOOR) * self.step

@dataclass
class Book:
    bid: D
    ask: D
    bid_size: D
    ask_size: D
    timestamp_us: int
    received: float
    @property
    def mid(self): return (self.bid + self.ask) / 2
    @classmethod
    def parse(cls, row):
        bid, ask = row["bestBid"], row["bestAsk"]
        if not bid or not ask: raise ValueError("empty BBO")
        b = cls(number(bid["price"]), number(ask["price"]), number(bid["size"]),
                number(ask["size"]), int(row["timestamp"]), time.monotonic())
        if not 0 < b.bid < b.ask or min(b.bid_size, b.ask_size) <= 0: raise ValueError("invalid BBO")
        return b

@dataclass
class Quote:
    slot: str
    side: str
    price: D
    qty: D
    reduce: bool = False

@dataclass
class Order:
    quote: Quote
    client_id: str
    remaining: D
    created: float
    order_id: str = ""
    status: str = "PENDING"
    sequence: int = -1
    rejection_reason: str | None = None

class Ledger:
    def __init__(self, capital):
        self.initial = number(capital)
        self.qty = self.entry = self.realized = self.fees = self.volume = self.maker_volume = D(0)
    def fill(self, side, qty, price, fee, maker=True):
        qty, price, fee = number(qty), number(price), number(fee)
        if side not in ("BUY", "SELL") or qty <= 0 or price <= 0: raise ValueError("invalid fill")
        delta = qty if side == "BUY" else -qty
        old = self.qty
        if old * delta >= 0:
            self.entry = (abs(old) * self.entry + qty * price) / (abs(old) + qty)
        else:
            self.realized += min(abs(old), qty) * (price - self.entry) * (1 if old > 0 else -1)
            if qty > abs(old): self.entry = price
        self.qty += delta
        if not self.qty: self.entry = D(0)
        self.fees += fee
        self.volume += qty * price
        if maker: self.maker_volume += qty * price
    def unrealized(self, px): return self.qty * (px - self.entry)
    def equity(self, px): return self.initial + self.realized - self.fees + self.unrealized(px)
    def report(self, book, taker_fee):
        px = book.bid if self.qty > 0 else book.ask
        pnl = self.realized - self.fees + self.unrealized(px)
        exit_fee = abs(self.qty) * px * taker_fee
        wear = max(D(0), -pnl + exit_fee)
        return dict(position=self.qty, average_entry=self.entry, realized_pnl=self.realized, fees=self.fees,
                    unrealized_pnl=self.unrealized(book.mid), volume_usd=self.volume, maker_volume_usd=self.maker_volume,
                    estimated_exit_pnl=pnl-exit_fee, estimated_wear_usd=wear,
                    wear_per_dollar=wear/self.volume if self.volume else None)

class Log:
    def __init__(self, directory, mode):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.path = Path(directory) / (mode + "-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
        self.mode = mode
        self.recent = deque(maxlen=200)
    def __call__(self, event, **values):
        line = json.dumps(dict(time_ns=time.time_ns(), mode=self.mode, event=event, **values), default=str, ensure_ascii=False)
        self.recent.append(json.loads(line))
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as f: f.write(line + "\n")
