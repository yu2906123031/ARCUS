> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Authentication

> Ed25519 API keys and request signing

Arcus is non-custodial. The server holds only the public half of your API key, and registration is gated by an ECDSA signature from the master Ethereum address that owns the key.

## Generate an Ed25519 key pair

The public key becomes your `apiKey`. The [Arcus web app](https://testnet.arcus.xyz/api-keys) can generate and register a key for you in a few clicks — the easiest path, and where the [trading guides](/guides/rest-trading#step-2-get-an-api-key) start; the manual steps below are for managing keys yourself.

```bash theme={null}
openssl genpkey -algorithm ed25519 -out private.pem
API_KEY=$(openssl pkey -in private.pem -pubout -outform DER | tail -c 32 | xxd -p -c 32)
```

## Register the key (REST)

The registration request must be ECDSA-signed by the Ethereum address that owns the key.

```bash theme={null}
curl -X POST https://api.testnet.arcus.xyz/v1/createApiKey \
  -H "Content-Type: application/json" \
  -d "{
    \"address\": \"0x...\",
    \"publicKey\": \"$API_KEY\",
    \"apiWalletName\": \"Arcus\",
    \"validUntil\": 1777479871997,
    \"signature\": { \"r\": \"0x...\", \"s\": \"0x...\", \"v\": \"0x1b\" }
  }"
```

The `signature` is the `(r, s, v)` triple from the wallet's **EIP-712 typed-data signature** (`eth_signTypedData_v4`) — see the [`POST /v1/createApiKey`](/api-reference/onboarding/create-api-key) endpoint reference for the domain, types, and complete signing examples.

`createApiKey` is REST-only. The WebSocket server returns `501 NotImplemented` for it.

## Sign protected requests

Every mutating request is authenticated with your Ed25519 API key. You always send the **same three values** — only *what you put in the signature* changes per operation:

| Value | REST | WebSocket |
| - | - | - |
| API key (the public key) | `X-API-Key` header | `apiKey` envelope field |
| Unix-**nanosecond** timestamp | `X-Timestamp` header | `timestamp` envelope field |
| Ed25519 signature (128 hex chars) | `X-Signature` header | `signature` envelope field |

There are **two signing schemes**. Pick by operation:

| Operation | Scheme |
| - | - |
| `placeOrder`, `cancelOrder`, `modifyOrder` | **Scheme 1 — typed payload** |
| `batchPlaceOrders`, `batchCancelOrders` | **Scheme 1**, applied once per element (see [Batches](#batches)) |
| `cancelAllOrders`, `setLeverage`, WebSocket `authenticate` | **Scheme 2 — legacy message** |
| Referral writes (`/v1/affiliate/*` POSTs) and signed referral reads (`inviteCodes`, `info`, `code`, `referees`, `commissions`) | **Scheme 2** (a read has no body, so it signs `timestamp + action`) |

Batches are **not** a third scheme — they are Scheme 1 signed separately for each order. Both schemes produce an Ed25519 signature; they differ only in which bytes you sign.

### Scheme 1 — typed payload (orders)

For `placeOrder`, `cancelOrder`, and `modifyOrder`, the signed message **is the request payload itself** — a compact, key-sorted JSON object built from engine-native integer values. There is no timestamp or action prefix:

```
signature = ed25519(canonical_payload)
```

The timestamp lives *inside* the payload as `ct` (it must equal the `X-Timestamp` you send). Keys are in fixed alphabetical order with no whitespace. The address (`ad`) is the **only** case-folded field — it is lowercased before signing. Every other string is signed **exactly as you send it**: the client id (`c`) and the order id (`id`) are byte-for-byte verbatim, so a mixed-case `clientId` must be signed in its original case and sent in that same case, or verification fails.

| Operation | `op` | Canonical payload |
| - | - | - |
| `placeOrder` | `1` | `{"ad":"0x…","ai":N,"c":"…","ct":N,"g":N,"m":N,"op":1,"p":N,"q":N,"r":0,"s":N,"t":N,"v":1}` |
| `cancelOrder` | `2` | `{"ad":"0x…","ai":N,"c":"…","ct":N,"id":"…","m":N,"op":2,"v":1}` |
| `modifyOrder` | `3` | `{"ad":"0x…","ai":N,"c":"…","ct":N,"g":N,"id":"…","m":N,"op":3,"p":N,"q":N,"r":0,"s":N,"t":N,"v":1}` |

Fields:

* `ad` — master Ethereum address, lowercase `0x`-hex
* `ai` — account (subaccount) index
* `c` — client id; **omitted entirely when empty**
* `ct` — client timestamp in Unix **nanoseconds**; equals the `X-Timestamp` you send
* `g` — `goodTilTime` in **nanoseconds** (the request's microsecond `goodTilTime` × 1000; required on every order — see below) — `placeOrder` and `modifyOrder`
* `id` — server `orderId`. **Required** for `modifyOrder`; for `cancelOrder` it is **omitted when canceling by client id**
* `m` — market id
* `op` — operation: `1` place, `2` cancel, `3` modify (`4` = untriggered TPSL — same fields as place)
* `p` — price in **integer ticks** = `price ÷ market tickSize`
* `q` — quantity in **integer quantums** = `size ÷ market stepSize`
* `r` — reduce-only, `0` or `1` (integer, not a boolean) — `placeOrder` and `modifyOrder`
* `s` — side: `0` buy, `1` sell — `placeOrder` and `modifyOrder`
* `t` — time-in-force: `0` GTT, `1` FOK, `2` IOC, `3` ALO — `placeOrder` and `modifyOrder`
* `v` — payload version (currently `1`)

For `cancelOrder`, provide exactly one of `id` (server order id) or `c` (client id). For `modifyOrder`, `id` is **always required**; include `c` only if the resting order was placed with a client id (it echoes that original value, and the engine rejects a mismatch). The `g`, `r`, `s`, and `t` fields in a `modifyOrder` **echo the resting order's immutable attributes** so the validator can verify the signature without fetching the original — passing a new `g` performs a cancel-replace with a fresh expiry. Because `p` and `q` are integers, convert the human-readable decimal price and size to ticks and quantums using the market's `tickSize` and `stepSize` from [`GET /v1/markets`](/api-reference/public/get-markets) — the conversion must be exact (`price ÷ tickSize` with no remainder). The divisor is **always the market's top-level `tickSize`**, at every price — a market's `tickTiers` ladder never changes the signed integer. Tiers only constrain which prices are *accepted*: a limit price in a coarser band must also be a multiple of that band's tick or the order is rejected (`… is not a multiple of tick size … for its price tier`); it is never re-interpreted under a different denominator, so code that signs with `tickSize` stays correct even after a market's price crosses a tier boundary.

**Every order requires `goodTilTime` / `g`** — including `1` FOK and `2` IOC — and it must be **at least one month in the future** when the API handler processes the order; nearer or missing values are rejected. FOK and IOC orders never rest, but the field still acts as a mandatory replay-protection timestamp on them.

#### Batches

A batch (`batchPlaceOrders` / `batchCancelOrders` / `batchModifyOrders`) is just Scheme 1 repeated: sign **each element** as its own typed payload, with every element sharing the one `X-Timestamp` you send as its `ct`. Each element carries its own `signature` field, and the shared `X-API-Key` + `X-Timestamp` authenticate the request. On REST the `X-Signature` header must still be **present** — set it to any one element's signature; the batch is verified per element, not against this header (omitting it rejects every element with `invalid order signature`). A top-level `grouping` (TPSL) field is not signed.

### Scheme 2 — legacy message (everything else)

`cancelAllOrders`, `setLeverage`, WebSocket `authenticate`, and the [referral endpoints](/api-reference/referral/get-affiliate-info) sign the timestamp, then the action, then the canonical JSON body — concatenated with no delimiters:

```
signature = ed25519(timestamp + action + canonical_json(body))
```

`action` is the camelCase final path segment (`/v1/cancelAllOrders` → `cancelAllOrders`); over WebSocket it's the request `type`. A signed `GET` has no body, so its message is `timestamp + action` (e.g. `/v1/affiliate/inviteCodes` → `inviteCodes`), and its `address` query parameter must be the API key's master Ethereum address. The HTTP method is not part of the message. `canonical_json` serializes the body with sorted keys and no whitespace.

You do not need to call `authenticate` first for signed requests.

## Your testnet account

A new account starts **empty**. `POST /v1/createApiKey` registers your key but credits no balance, so [`GET /v1/account`](/api-reference/public/get-account) returns `404 "this account has no activity yet"` until your first deposit. (Testnet previously auto-credited \$5,000 of paper-trading collateral; that has been removed.)

Most testnet users never fund manually: in the Arcus testnet web app, connect your wallet and click **Testnet Deposit** to instantly credit **\~\$1,000 USDC** of paper-trading collateral. If you trade via the API and need a larger balance — or want to fund programmatically — deposit collateral on-chain instead; see [Fund a testnet account](/guides/fund-testnet-account).

Once funded, the deposit appears as a `DEPOSIT` entry in [`GET /v1/accountTransferUpdates`](/api-reference/public/get-account-transfer-updates) and your balance shows up on `GET /v1/account`:

```bash theme={null}
curl "https://api.testnet.arcus.xyz/v1/account?address=0x..." \
  -H "X-API-Key: $API_KEY"
# → { "equity": "...", "netDeposits": "...", "freeCollateral": "...", ... }
```

Testnet trades against a paper-trading universe of perpetuals across crypto, equities, commodities, and indices — all quoted against USD. The full list lives at [`GET /v1/markets`](/api-reference/public/get-markets); subscribe to the [`markets`](/api-reference/channels#markets) WebSocket channel for live updates. The universe is the same for every API key.


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.