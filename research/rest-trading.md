> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Trade over REST

> Register a key and place your first order over REST — the simplest way to get started

REST is the simplest way to place your first order: one signed request per call, no socket to manage. This quickstart gets you from nothing to a live order on testnet in three steps.

<Note>
  For production or low-latency trading, use the [WebSocket track](/guides/websocket-trading) — the primary interface. Requests target testnet (`https://api.testnet.arcus.xyz`); new accounts start empty — fund yours with the **Testnet Deposit** button (\~\$1,000), or an [on-chain deposit](/guides/fund-testnet-account) for more. See [Authentication](/api-reference/authentication) for signing.
</Note>

## Step 1: Install

Only published libraries — no Arcus package required.

```bash theme={null}
pip install cryptography eth-account
```

## Step 2: Get an API key

Your API key is an **Ed25519 keypair**; the server stores only the public half, and the key is registered to your wallet by a one-time **EIP-712 typed-data signature** (`eth_signTypedData_v4`) — see [`POST /v1/createApiKey`](/api-reference/onboarding/create-api-key) for the domain and types (the legacy EIP-191 scheme is deprecated but still accepted during the migration window). The Arcus web app does all of this for you and is the quickest way to get a key; you can also do it entirely in code.

### Option A — Generate on the web app (recommended)

1. Open the API Keys page — [testnet](https://testnet.arcus.xyz/api-keys), or [mainnet](https://app.arcus.xyz/api-keys) for production — and connect the wallet you'll trade from.
2. Enter a name and click **Generate**. The app creates the keypair in your browser.
3. **Copy the API Signing Key now** — it's the private half, shown once, and cannot be recovered. Pick a **Subaccount #** (default `0`) and **Days Valid** (up to 180), then click **Authorize** and confirm the signature in your wallet.

That registers the key — nothing to run. Load the signing key you copied and skip to [Step 3](#step-3-place-an-order):

```python theme={null}
# guide-test: skip — placeholder values; Option B is the runnable path.
from cryptography.hazmat.primitives.asymmetric import ed25519

priv = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex("<API Signing Key>"))
api_key = priv.public_key().public_bytes_raw().hex()   # matches the "API Key" the app shows
account_index = 0                 # the Subaccount # you selected
wallet_address = "0x..."          # the wallet you connected
```

The steps below read the master address as `wallet.address`; with a web-app key, use your `wallet_address` wherever you see it.

### Option B — Generate in code

Generate a fresh keypair and register it with one `createApiKey` call — the same operation the web app performs:

```python theme={null}
import json, time, urllib.request
from cryptography.hazmat.primitives.asymmetric import ed25519
from eth_account import Account
from eth_account.messages import encode_defunct

BASE = "https://api.testnet.arcus.xyz"

def canonical(obj):
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()

def call(method, path, body=None, headers=None):
    data = canonical(body) if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")

priv = ed25519.Ed25519PrivateKey.generate()
api_key = priv.public_key().public_bytes_raw().hex()
wallet = Account.create()   # or Account.from_key("0x...") to reuse an address

valid_until = int(time.time() * 1000) + 7 * 86_400_000   # within [now+1d, now+180d]
msg = canonical({"apiWalletName": "quickstart", "apiWalletPublicKey": api_key, "validUntil": valid_until})
sig = wallet.sign_message(encode_defunct(primitive=msg))
created = call("POST", "/v1/createApiKey", {
    "address": wallet.address, "publicKey": api_key, "apiWalletName": "quickstart",
    "validUntil": valid_until, "signature": {"r": hex(sig.r), "s": hex(sig.s), "v": hex(sig.v)}})
account_index = created["accountIndex"]

# A new key takes a moment to start authenticating — wait for it.
for _ in range(60):
    if any(k["apiKey"] == api_key for k in call("GET", f"/v1/apiKeys?address={wallet.address}")["apiKeys"]):
        break
    time.sleep(1)
print("key is live, accountIndex:", account_index)
```

## Step 3: Place an order

Order requests sign a **typed canonical payload** — the compact, key-sorted JSON object shown below **is** the signing message (no prefix). The timestamp is carried inside as `ct` and must equal the `X-Timestamp` header (Unix **nanoseconds**). Price and size are signed as integer **ticks** and **quantums**, so you first convert them with the market's `tickSize` / `stepSize`. See [Authentication](/api-reference/authentication) for the full field reference.

```python theme={null}
from decimal import Decimal

OP_PLACE, OP_CANCEL = 1, 2
SIDE = {"BUY": 0, "SELL": 1}
TIF  = {"GTT": 0, "FOK": 1, "IOC": 2, "ALO": 3}

# BTC-USD (marketId 1): tick/step sizes convert price & size to engine integers.
mkt  = next(m for m in call("GET", "/v1/markets")["markets"] if m["marketId"] == 1)
TICK, STEP = mkt["tickSize"], mkt["stepSize"]
addr = wallet.address.lower()   # the signed payload uses the lowercase address

def to_int(value, unit):        # exact decimal → integer ticks/quantums
    n = Decimal(str(value)) / Decimal(unit)
    if n != n.to_integral_value():
        raise ValueError(f"{value} is not a multiple of {unit}")
    return int(n)

def post_order(path, payload, body, ts):
    # The typed payload IS the signing message — sign it directly, no prefix.
    sig = priv.sign(payload.encode()).hex()
    return call("POST", path, body,
        {"X-API-Key": api_key, "X-Timestamp": str(ts), "X-Signature": sig})

# A resting GTT buy ~5% below the BTC-USD oracle price, snapped to the tick.
on_tick   = round(float(mkt["oraclePrice"]) * 0.95 / float(TICK)) * float(TICK)
price     = round(on_tick, 10)
good_til  = int(time.time() * 1_000_000) + 40 * 86_400 * 1_000_000   # epoch µs, ≥1 month ahead
ts        = time.time_ns()

payload = (f'{{"ad":"{addr}","ai":{account_index},"ct":{ts},"g":{good_til * 1000},'
           f'"m":1,"op":{OP_PLACE},"p":{to_int(price, TICK)},"q":{to_int("0.001", STEP)},'
           f'"r":0,"s":{SIDE["BUY"]},"t":{TIF["GTT"]},"v":1}}')
placed = post_order("/v1/placeOrder", payload, {
    "address": wallet.address, "accountIndex": account_index, "marketId": 1,  # BTC-USD
    "orderSide": "BUY", "orderType": "LIMIT", "quantity": "0.001", "price": str(price),
    "timeInForce": "GTT", "goodTilTime": str(good_til), "timestamp": ts}, ts)
print("placed:", placed["orderId"], placed["status"])

# Cancel by orderId — cancelOrder signs its own payload (op 2; no g/p/q/s/t fields).
ts = time.time_ns()
payload = (f'{{"ad":"{addr}","ai":{account_index},"ct":{ts},'
           f'"id":"{placed["orderId"]}","m":1,"op":{OP_CANCEL},"v":1}}')
canceled = post_order("/v1/cancelOrder", payload, {
    "address": wallet.address, "accountIndex": account_index, "marketId": 1,
    "kind": "orderId", "orderId": placed["orderId"], "timestamp": ts}, ts)
print("canceled:", canceled["status"])
```

`placeOrder`, `cancelOrder`, and `modifyOrder` use the typed payload above; `cancelAllOrders` and `setLeverage` use the legacy `timestamp + action + canonicalJSON(body)` message — see [Authentication](/api-reference/authentication).

<Note>
  In the request **body**, `quantity` and `price` stay **decimal strings** (not floats). The integer `q`/`p` in the signed payload are derived from them via `tickSize` / `stepSize`; the two must agree, so reuse the same values.
</Note>

<Note>
  **Market orders** must use `timeInForce: IOC` (other values are rejected with `must be IOC for MARKET orders`) and still carry a `price` — it acts as a protective slippage bound that must be within **10%** of the current mark price (for TPSL market orders, within 10% of the trigger price), or the order is rejected. Because of this bound a `MARKET` order behaves like a marketable limit order, so many integrations simply send an aggressive `LIMIT` order instead.
</Note>

## Step 4: Place a batch

`batchPlaceOrders` signs **each order individually** with its own typed payload, all sharing one `X-Timestamp` (used as each element's `ct`). The `X-API-Key` + `X-Timestamp` headers authenticate the batch and each order carries its own `signature`. The `X-Signature` header must still be **present** — set it to any one element's signature (the batch is verified per element, not against this header).

```python theme={null}
ts = time.time_ns()

def batch_order(side, price, qty):
    payload = (f'{{"ad":"{addr}","ai":{account_index},"ct":{ts},"g":{good_til * 1000},'
               f'"m":1,"op":{OP_PLACE},"p":{to_int(price, TICK)},"q":{to_int(qty, STEP)},'
               f'"r":0,"s":{SIDE[side]},"t":{TIF["GTT"]},"v":1}}')
    return {"address": wallet.address, "accountIndex": account_index, "marketId": 1,
            "orderSide": side, "orderType": "LIMIT", "quantity": qty, "price": str(price),
            "timeInForce": "GTT", "goodTilTime": str(good_til), "timestamp": ts,
            "signature": priv.sign(payload.encode()).hex()}

orders = [
    batch_order("BUY", round(price * 0.99, 1), "0.001"),
    batch_order("BUY", round(price * 0.98, 1), "0.001"),
]
# X-Signature must be present on a batch — set it to any element's signature.
batch = call("POST", "/v1/batchPlaceOrders", {"orders": orders},
    {"X-API-Key": api_key, "X-Timestamp": str(ts), "X-Signature": orders[0]["signature"]})
print("batch placed:", len(batch["responses"]))
```

`batchCancelOrders` works the same way — each element signs its own `cancelOrder` payload (`op` 2).

## Next steps

Order placement is asynchronous — the response acknowledges receipt, and the order's lifecycle (fills, cancels) streams on the `orders` and `userFills` WebSocket channels. To follow orders in real time, move to the [WebSocket track](/guides/websocket-trading).


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.