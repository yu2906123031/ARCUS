"""Official protocol, independently implemented.
https://docs.arcus.xyz/api-reference/authentication
https://docs.arcus.xyz/api-reference/exchange/place-order
https://docs.arcus.xyz/api-reference/exchange/cancel-order
"""
import json
import os
import re
from decimal import Decimal
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def exact_units(value, step):
    units = Decimal(value) / step
    if not units.is_finite() or units != units.to_integral_value(): raise ValueError("not exactly tick/step aligned")
    return int(units)

def typed(action, body, timestamp, market):
    if int(body.get("timestamp", timestamp)) != timestamp: raise ValueError("timestamp mismatch")
    p = dict(ad=body["address"].lower(), ai=body["accountIndex"], ct=timestamp,
             m=body["marketId"], op={"placeOrder":1, "cancelOrder":2}[action], v=1)
    if body.get("clientId"): p["c"] = body["clientId"]  # Preserve case; only ad is lowercased.
    if action == "cancelOrder":
        if bool(body.get("orderId")) == bool(body.get("clientId")): raise ValueError("exactly one cancel target required")
        if body.get("orderId"): p["id"] = body["orderId"]
    else:
        # HTTP goodTilTime is microseconds; signed g is nanoseconds.
        p.update(g=int(body["goodTilTime"])*1000, p=exact_units(body["price"],market.tick),
                 q=exact_units(body["quantity"],market.step), r=int(body.get("reduceOnly",False)),
                 s={"BUY":0,"SELL":1}[body["orderSide"]], t={"GTT":0,"FOK":1,"IOC":2,"ALO":3}[body["timeInForce"]])
    return canonical(p)

class Signer:
    def __init__(self):
        self.address = os.environ["ARCUS_ADDRESS"]
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}",self.address): raise ValueError("invalid ARCUS_ADDRESS")
        self.address = self.address.lower()
        seed = os.environ["ARCUS_API_PRIVATE_KEY"].removeprefix("0x")
        if not re.fullmatch(r"[0-9a-fA-F]{64}",seed): raise ValueError("private key must be 32-byte Ed25519 seed hex")
        self.key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed))
        self.public = self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        self.account_index = int(os.environ.get("ARCUS_ACCOUNT_INDEX","0"))
        if not 0 <= self.account_index <= 9: raise ValueError("account index must be 0..9")
    def headers(self, action, body, timestamp, market):
        if action in ("placeOrder","cancelOrder"): message = typed(action,body,timestamp,market)
        elif action in ("cancelAllOrders","setLeverage","scheduleCancel"):
            # Scheme 2 also covers the dead man's switch.
            # https://docs.arcus.xyz/api-reference/websocket#dead-mans-switch-schedulecancel
            message = str(timestamp).encode() + action.encode() + canonical(body)
        else: raise ValueError("unsupported signed operation")
        return {"X-API-Key":self.public,"X-Timestamp":str(timestamp),"X-Signature":self.key.sign(message).hex()}
