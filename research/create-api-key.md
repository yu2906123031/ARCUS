> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Create API key

> Create an API key for the ethereum address.

<Note>**The response body shown here is a static example, not live data.** After you click **Send**, your live result appears in a separate panel headed **200 OK**. The panel under the status-code tabs is a fixed sample from the spec — its field values (fees, prices, sizes, IDs, timestamps) are placeholders. Use **Send**, or call the endpoint, for current values.</Note>

Create an API key for the ethereum address. No address or account parameter is required; all keys are associated with the master account.

## Signing the request

The request is authenticated by a secp256k1 **EIP-712** typed-data signature (`eth_signTypedData_v4`) produced by the wallet that owns `address`, so wallets render each field and institutional / MPC signers can whitelist on the domain. Sign the typed data below, then split the 65-byte result into `{r, s, v}` for the `signature` field. The gateway recovers the signer and rejects the request with HTTP 401 if the recovered address does not equal `address`.

**Domain**

API keys are pure off-chain authentication, so the domain has **no `verifyingContract`** (the field is optional per EIP-712):

```
{
  "name": "Arcus API Key",
  "version": "1",
  "chainId": <rootchain chain id, e.g. 4663>
}
```

**Types**

To scope the key to a subaccount, sign the accountIndex-bearing type. Use `0`–`9` for a single subaccount, or `255` to scope the key to **all** of the address's subaccounts (the default for web/mobile trading keys):

```
CreateApiKey(string apiWalletName,string apiWalletPublicKey,uint256 validUntil,uint8 accountIndex)
```

For `accountIndex` 0 you may instead sign the original index-less type (backward compatibility, no deadline); a **non-zero** `accountIndex` (including the `255` all-subaccounts sentinel) MUST use the type above:

```
CreateApiKey(string apiWalletName,string apiWalletPublicKey,uint256 validUntil)
```

**Replay protection (recommended)**

When including a `nonce` in the request body, sign the combined type that binds both replay protection and subaccount scope:

```
CreateApiKey(string apiWalletName,string apiWalletPublicKey,uint256 validUntil,string nonce,uint8 accountIndex)
```

The `nonce` and `accountIndex` in the message must match the request body. For `accountIndex` 0 the server also accepts the nonce-only type without `accountIndex` in the signed payload (backward compatibility).

**Message**

```
{
  "apiWalletName": "<apiWalletName>",
  "apiWalletPublicKey": "<publicKey>",
  "validUntil": <validUntil>,
  "accountIndex": <accountIndex>
}
```

`apiWalletPublicKey` is the request's `publicKey` field (64 hex chars, no `0x`); `validUntil` is epoch ms, the same value as the request field. Always send `validUntil` explicitly and sign that value — if the body omits it, the server verifies against its own default (now + 14 days), which will not match what you signed. `accountIndex` is the key's subaccount scope — `0`–`9` for a single subaccount, or `255` for all subaccounts — and must match the request field; omit it from the message only when signing the index-less type for index 0. The legacy EIP-191 `personal_sign` fallback is index-0 only.

**Per-environment domain parameters**

| Environment | `chainId` |
| - | - |
| testnet | `46630` |
| production | `4663` |

**Complete `eth_signTypedData_v4` call (ethers v6 / viem)**

```js
// ethers v6
const domain = {
  name: "Arcus API Key",
  version: "1",
  chainId: 4663                            // production
};
const types = {
  CreateApiKey: [
    { name: "apiWalletName",      type: "string"  },
    { name: "apiWalletPublicKey", type: "string"  },
    { name: "validUntil",         type: "uint256" }
  ]
};
const message = {
  apiWalletName:      "Arcus",
  apiWalletPublicKey: pubKeyHex,           // 64 hex chars, no 0x
  validUntil:         validUntil           // epoch ms
};
const sig = await signer.signTypedData(domain, types, message);
// Split into r / s / v for the request body:
const r = sig.slice(0, 66);
const s = "0x" + sig.slice(66, 130);
const v = "0x" + sig.slice(130, 132);
```

```ts
// viem
const sig = await walletClient.signTypedData({ domain, types, primaryType: "CreateApiKey", message });
```

### Legacy EIP-191 signatures (deprecated)

This endpoint previously accepted an EIP-191 `personal_sign` signature over the canonical JSON message

```
{"apiWalletName":"<apiWalletName>","apiWalletPublicKey":"<publicKey>","validUntil":<validUntil>}
```

(keys in this exact order, no whitespace, no trailing newline). During the migration window the server accepts **either** scheme: it verifies the EIP-712 signature first and falls back to legacy EIP-191. EIP-191 is deprecated and will be **rejected once the migration window closes** — migrate existing integrations to EIP-712 typed data; new integrations must use EIP-712 only.

## Public-key ownership

A public key is a globally unique credential: auth resolves the owning account from `api_keys_by_key`. `createApiKey` rejects registering a public key already owned by a **different** account with HTTP 409 — including keys that have expired but were never revoked. Re-registering a key the caller already owns (renew / rewrite `validUntil`) is allowed.


## OpenAPI

````yaml /api-reference/openapi.yml post /v1/createApiKey
openapi: 3.1.0
info:
  title: Arcus API
  version: 1.0.0
  description: >
    REST API for the Arcus exchange. Provides endpoints for order management,
    market data, and account onboarding.


    ## Authentication

    Order management and credential-creating endpoints require **three** request
    headers:


    - `X-API-Key` — hex-encoded Ed25519 public key (64 chars). The public key
    *is* the API key. Obtain/register one via `POST /createApiKey`.

    - `X-Timestamp` — Unix time in **nanoseconds** as a decimal string (e.g.
    `"1713825891591000000"`). Millisecond or second epochs are rejected with
    `401 Unauthorized`. Must be within ±30,000 ms of server wall-clock;
    otherwise the request is rejected with `401 Unauthorized`.

    - `X-Signature` — lowercase hex-encoded Ed25519 signature (128 chars).


    **Single-order endpoints** (`placeOrder`, `cancelOrder`, `modifyOrder`) use
    the **ordersign typed canonical payload** as the signing message — a
    compact, key-sorted JSON object with engine-native integer values. The
    payload is NOT the raw HTTP body; it is built from the parsed request
    fields:

      ```
      placeOrder:   {"ad":"0x…","ai":N,[,"c":"…"],"ct":N,"g":N,"m":N,"op":1,"p":N,"q":N,"r":0|1,"s":N,"t":N,"v":1}
      cancelOrder:  {"ad":"0x…","ai":N,[,"c":"…"],"ct":N,[,"id":"…"],"m":N,"op":2,"v":1}
      modifyOrder:  {"ad":"0x…","ai":N,[,"c":"…"],"ct":N,"g":N,[,"id":"…"],"m":N,"op":3,"p":N,"q":N,"r":0|1,"s":N,"t":N,"v":1}   (exactly one of id / c)
      ```

    `ct` in the payload must equal `X-Timestamp`. Integer values `p`, `q` etc.
    are engine-native ticks/quantums, not human-readable decimals. Keys in
    brackets are conditional (omitted when empty). `op` is the operation enum:
    `1`=place, `2`=cancel, `3`=modify, `4`=placeUntriggered (TPSL). See the
    signing reference below for the full field definitions and reference signing
    code.


    **Batch endpoints still require this header to be present** (its value is
    not verified) — see below.

    - **Batch endpoints are signed per order, but the `X-Signature` header must
    still be present.** `POST /batchPlaceOrders`, `POST /batchCancelOrders`, and
    `POST /batchModifyOrders` require `X-API-Key` + `X-Timestamp` +
    `X-Signature`; the gateway does **not** verify the envelope `X-Signature`
    *value* for these routes (set it to any one element's signature), but
    omitting it — or sending an empty value — rejects every element with
    `invalid order signature`. Instead, each element of the `orders` / `cancels`
    / `modifies` array embeds its own `"signature"` field — a 128-hex Ed25519
    signature over the **ordersign typed canonical payload** for that operation:

        - Plain `orders` elements (no `tpsl_type`) use `op=1`.
        - TPSL / conditional `orders` elements (with `tpsl_type`) use `op=4`
          (`OpPlaceUntriggered`) — same field set as `op=1` but a different `op` value,
          preventing replay of a TPSL signature as a plain placeOrder.
        - `cancels` elements use `op=2`.
        - `modifies` elements use `op=3` — the same payload as a standalone `modifyOrder`.

    The `ct` field inside every element's payload must equal the shared
    `X-Timestamp` header value. The whole batch consumes a single replay slot.


    Read-only endpoints (`GET /openOrders`, `GET /fills`, etc.) require only
    `X-API-Key`. In relaxed local development the gateway may run with
    signatures disabled; the OpenAPI `security` blocks list `signedRequest` (the
    three-header AND) or `{}` (empty) to reflect that.


    ## Account-scoped requests

    Public account info reads such as fills, positions, orders, open orders, and
    account snapshots require `address` as a **query parameter**. Signed order
    management requests also require `address`, and it must match the master
    Ethereum address returned for that API key from `POST /createApiKey`.
    WebSocket `placeOrder` / `cancel` / `batchPlaceOrders` / `batchCancelOrders`
    / `modifyOrder` / `batchModifyOrders` use the same value in the JSON payload
    field `address`.


    ## Order execution is asynchronous

    `POST /placeOrder`, `POST /cancelOrder`, `POST /batchPlaceOrders`, `POST
    /batchCancelOrders`, and `POST /batchModifyOrders` are asynchronous. They
    return either:


    - **`202 Accepted`** — the common case. The signed request was validated by
    the gateway and forwarded to the matching engine; the body does **not**
    carry the order's terminal state.

    - **`200 OK`** — returned only when the gateway already has definitive state
    for the request by the time it responds (the body's `status` reflects that
    state, e.g. `OPEN` / `FILLED` / `CANCELED` / `REJECTED`). Clients should
    treat this as best-effort enrichment and not rely on it.


    In both cases the body echoes `orderId` / `clientId` so callers can
    correlate WebSocket events with the request. To observe the full order
    lifecycle (`OPEN`, `FILLED`, `CANCELED`, `REJECTED`, fills, rejection
    reasons, etc.) clients **must** subscribe to the `orders` WebSocket channel
    (and `userFills` for trade-level events). Treat both the `202` and `200`
    HTTP bodies as request acknowledgements; all definitive state lives on the
    WebSocket.


    ## Order rejection reasons

    When the matching engine rejects an order, the resulting `AccountUpdate`
    event (delivered over the WebSocket `account` channel with `type: REJECTED`)
    carries a `rejectionReason` enum. The same value may also appear on HTTP
    `OrderResponse` / `CancelOrderResponse` bodies that resolved to definitive
    state before returning (the `200` path described above). The complete set of
    reasons the matching engine can emit today is:


    | Value                        |
    Meaning                                                                 |

    |------------------------------|-------------------------------------------------------------------------|

    | `POST_ONLY_WOULD_CROSS`      | Post-only order was rejected because it
    would have crossed the book and taken liquidity. |

    | `SELF_TRADE`                 | Order would have matched against another
    resting order belonging to the same account; self-trade prevention rejected
    it. |

    | `UNDERCOLLATERALIZED`        | Account has insufficient free collateral /
    margin to support the order. |

    | `COULD_NOT_FILL`             | Generic "could not fill" outcome. Legacy
    reason — prefer the IOC/FOK-specific values below for time-in-force-driven
    cancels. |

    | `IOC_CANCELED`               | An `IMMEDIATE_OR_CANCEL` order produced
    zero fills against the book and was canceled. |

    | `FOK_FAILED`                 | A `FILL_OR_KILL` order could not be filled
    in full at submission and was canceled in its entirety. |

    | `REDUCE_ONLY_WOULD_INCREASE` | A `reduceOnly` order was rejected because
    executing it would have opened or increased the account's position rather
    than reducing it. |

    | `TOO_MANY_CLIENT_IDS`        | Account already has 10,000 live `clientId`s
    (the per-account maximum). Cancel an existing order or wait for one to reach
    a terminal state to free a slot. |

    | `DUPLICATE_CLIENT_ID`        | The `clientId` already maps to a live order
    on this account. Cancel the prior order (or change it in place via
    `modifyOrder`) before reusing the same `clientId`. |


    See the `RejectionReason` schema for the canonical enum.


    ## Rate limiting

    Rate-limited endpoints return `429 Too Many Requests` carrying two
    retry-after signals:


    - `Retry-After` HTTP header (integer seconds, RFC 7231 compliant).

    - `retryAfterMs` field in the JSON body — precise milliseconds.


    The body also carries a typed `reason` indicating which layer rejected
    (`ip`, `account_empty`, `account_partial`). See `RateLimitedError` for the
    schema and `TooManyRequests` for the behavioral contract.
servers:
  - url: https://api.arcus.xyz
    description: Mainnet
  - url: https://api.testnet.arcus.xyz
    description: Testnet
security: []
tags:
  - name: Onboarding
    description: Account and API key management.
  - name: Public
    description: >-
      Unauthenticated endpoints — market data, health checks, and account-scoped
      info reads.
  - name: Exchange
    description: >
      Signed order management endpoints (all are HTTP `POST`). Every request
      must carry the full header triple `X-API-Key` + `X-Timestamp` +
      `X-Signature` where `X-Signature` is an Ed25519 signature over the
      operation-specific signing message: `placeOrder`, `cancelOrder`, and
      `modifyOrder` sign the ordersign typed canonical payload (the JSON object
      itself, no prefix), while `cancelAllOrders` and `setLeverage` use the
      legacy `X-Timestamp + ACTION + canonicalJSON(body)` message (ACTION = the
      final camelCase path segment; the HTTP method is not signed).
      `X-Timestamp` is a Unix nanosecond epoch (decimal string) and must be
      within ±30,000 ms of server wall-clock. See the top-level
      **Authentication** section for full details. These endpoints also require
      query parameter `address` matching the key's master Ethereum address.
  - name: Referral
    description: >
      Affiliate / referral program endpoints. Mutating endpoints (`POST`)
      require the full Ed25519 signature triple and the body `address` must
      match the API key's master Ethereum address.


      Most read endpoints (`GET`) are signed too, and `?address=` must match the
      API key's master Ethereum address — so a caller only ever reads their own
      referral data. That covers `/info`, `/code`, `/referees` and
      `/commissions`.


      Still public and scoped by `?address=`: `/myReferrer` (returns the
      binding's code and discount, never the referrer's address), `/claims`,
      `/claimStatus`, `/codeAvailable` (returns only whether a code is taken,
      never its owner) and `/leaderboard`.
paths:
  /v1/createApiKey:
    post:
      tags:
        - Onboarding
      summary: Create API key
      description: Create an API key for the ethereum address.
      operationId: createApiKey
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateApiKeyRequest'
      responses:
        '202':
          description: >
            API key creation accepted. The request has been accepted but the key
            is not yet usable for authenticated requests; the caller must wait
            briefly for it to be ingested before the returned `api_key`
            resolves.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CreateApiKeyResponse'
        '400':
          description: >-
            Validation error (missing fields, malformed `(r, s, v)` signature,
            bad address).
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '401':
          description: >-
            Signature verification failed. The address recovered from
            `signature` (EIP-712, or legacy EIP-191 during the migration window)
            did not match `address`.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '409':
          description: >-
            Conflict: `publicKey` is already registered to a different account,
            or the signed `nonce` was already consumed (replay). A public key is
            globally unique — only the owning account may register or renew it.
            This includes expired-but-unrevoked keys still reserved in
            persistence.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '429':
          $ref: '#/components/responses/TooManyRequests'
        '500':
          description: Internal error (e.g. failed to persist API key).
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
components:
  schemas:
    CreateApiKeyRequest:
      type: object
      required:
        - address
        - publicKey
        - apiWalletName
        - signature
      properties:
        address:
          $ref: '#/components/schemas/EthereumAddressHex'
          description: Ethereum address to associate with the account.
          examples:
            - '0x742d35cc6634c0532925a3b844bc9e7595f2bd18'
        publicKey:
          type: string
          pattern: ^[0-9a-fA-F]{64}$
          minLength: 64
          maxLength: 64
          description: Hex-encoded Ed25519 public key. Becomes the API key.
          examples:
            - a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
        apiWalletName:
          type: string
          minLength: 1
          maxLength: 64
          description: Name of the API wallet (included in the signing message).
          examples:
            - Arcus
        accountIndex:
          $ref: '#/components/schemas/ApiKeyAccountIndex'
          description: >-
            Subaccount scope for this key. `0`–`9` locks the key to that single
            subaccount; `255` scopes it to all of the address's subaccounts (the
            default for web/mobile trading keys). Defaults to 0 when omitted.
            The value is bound into the signed payload: any non-zero value
            (including `255`) MUST use the accountIndex-bearing EIP-712
            `CreateApiKey` type (see the endpoint description); index 0
            additionally accepts the legacy index-less type/signature for
            backward compatibility.
        validUntil:
          type: integer
          format: int64
          minimum: 1
          description: >-
            Expiration timestamp (epoch ms). Must be between 1 day and 180 days
            from the server's current time (inclusive). If omitted, defaults to
            14 days from now. Explicit values outside the [now+1d, now+180d]
            window are rejected with HTTP 400.
        nonce:
          type: string
          maxLength: 64
          description: >-
            Optional replay-protection nonce, single-use per `(address,
            accountIndex)` when included in the signed EIP-712 payload. Send the
            current time as nanoseconds since the Unix epoch (a plain base-10
            integer); the server accepts a timestamp up to 48 hours in the past
            and 24 hours in the future by default. A legacy opaque nonce (e.g. a
            UUID) is also accepted. Omitting `nonce` (or signing without it)
            skips replay protection during the migration window.
          examples:
            - a1b2c3d4-5f60-7182-93a4-b5c6d7e8f901
        signature:
          $ref: '#/components/schemas/EthereumSignature'
          description: >-
            secp256k1 **EIP-712** typed-data signature `(r, s, v)` over the
            `CreateApiKey` payload (`eth_signTypedData_v4`), produced by the
            wallet that owns `address`. See the `POST /v1/createApiKey` endpoint
            description for the exact domain, types, and message. During the
            migration window the deprecated legacy EIP-191 `personal_sign`
            scheme is also accepted. Requests where the recovered signer does
            not equal `address` are rejected with HTTP 401.
    CreateApiKeyResponse:
      type: object
      required:
        - apiKey
        - address
        - createdAt
      properties:
        apiKey:
          type: string
          description: Newly generated API key (hex string).
        address:
          $ref: '#/components/schemas/EthereumAddressHex'
        allSubaccounts:
          type: boolean
          description: >-
            True when the key trades every subaccount of `address` rather than a
            single one. Populated on every response; use it as the scope
            discriminator (optional in schema for deploy-order compatibility).
        accountIndex:
          $ref: '#/components/schemas/AccountIndex'
          description: >-
            The single subaccount (`0`–`9`) the key trades. Present only when
            `allSubaccounts` is false; omitted for an all-subaccounts key.
        validUntil:
          type: integer
          format: int64
          description: >-
            Expiration timestamp (epoch ms). Echoes the client-supplied expiry,
            so it stays in milliseconds. Absent if no expiry.
        createdAt:
          type: integer
          format: int64
          description: Creation timestamp (epoch microseconds).
    error:
      type: object
      required:
        - error
      properties:
        error:
          type: string
          description: Human-readable error message.
          examples:
            - Invalid request body
        code:
          type: string
          description: >
            Machine-readable error code for generic (non-order) rejections.
            Present on geo-restriction blocks; clients should key
            product-availability UI off this rather than the human `error`
            string.


            | Value            | Meaning |

            |------------------|---------|

            | `GEO_RESTRICTED` | The action is not available from the caller's
            country/region for this product (perps and/or spot). Reads remain
            available; only state-changing actions are blocked. |
          enum:
            - GEO_RESTRICTED
        errorSource:
          type: string
          description: >
            Scopes the failure to a specific operation. Present on structured
            order errors (place / cancel / modify flows); absent on generic HTTP
            errors.
          enum:
            - Order
            - Cancel
        errorType:
          type: string
          description: >
            Machine-readable reason for the API-layer rejection. Present
            alongside `errorSource` on structured order errors; absent on
            generic HTTP errors.


            | Value             | Meaning |

            |-------------------|---------|

            | `Tick`            | Price or size does not align to the market's
            tick size / step size. |

            | `InvalidRequest`  | A request field failed validation (bad
            address, unknown market, invalid enum, etc.). TPSL-specific causes:
            `stopPrice` required when `tpslType` is set; `tpslType` must be
            `STOP_LOSS` or `TAKE_PROFIT`; `reduceOnly` must be `true` for TPSL
            orders; trigger price must not sit on the wrong side of the current
            oracle (would fire immediately); batch grouping/shape constraints
            violated (wrong order count, missing or duplicate `tpslType` legs).
            |

            | `OracleDeviation` | The order's price deviates from the current
            oracle price by more than the allowed threshold. Adjust the price
            closer to the oracle and resubmit. |

            | `ReduceOnly`      | A reduce-only order was rejected because
            filling it would open or increase the account's position rather than
            reduce it. |

            | `Unavailable`     | The requested operation (place / cancel) is
            temporarily disabled. |

            | `Unauthorized`    | API key or trading-identity check failed. |

            | `Forbidden`       | The authenticated key is not permitted to
            perform the requested operation. |

            | `NotImplemented`  | The requested capability is not yet available
            on this path (e.g. TPSL over WebSocket). |

            | `Transmission`    | The gateway accepted and validated the request
            but could not deliver it to the matching engine. Retry. |

            | `Internal`        | Unexpected gateway-side failure unrelated to
            transmission. |
          enum:
            - Tick
            - InvalidRequest
            - OracleDeviation
            - ReduceOnly
            - Unavailable
            - Unauthorized
            - Forbidden
            - NotImplemented
            - Transmission
            - Internal
        rejectionReason:
          type: string
          description: >
            Machine-readable engine-level rejection code. Present on
            `OrderResponse` / `BatchOrderItemResponse` when `status` is
            `REJECTED` on the synchronous `200` path (i.e. the gateway already
            had a definitive engine reject before responding). Absent on `202`
            responses and on HTTP error bodies — in those cases rejection
            reasons are delivered asynchronously via the `orders` WebSocket
            channel.


            | Value                              | Meaning |

            |------------------------------------|---------|

            | `POST_ONLY_WOULD_CROSS`            | Post-only order would have
            crossed the book and taken liquidity. |

            | `SELF_TRADE`                       | Order would match against the
            account's own resting order; blocked by self-trade prevention. |

            | `UNDERCOLLATERALIZED`              | Account has insufficient free
            collateral / margin to support the order. |

            | `COULD_NOT_FILL`                   | Generic "could not fill"
            (legacy; prefer `IOC_CANCELED` / `FOK_FAILED`). |

            | `IOC_CANCELED`                     | IOC order produced zero fills
            and was canceled. |

            | `FOK_FAILED`                       | FOK order could not be fully
            filled at submission. |

            | `REDUCE_ONLY_WOULD_INCREASE`       | reduce-only order would have
            opened or increased the position. |

            | `TOO_MANY_CLIENT_IDS`              | Account already has 10,000
            live `clientId`s (the per-account maximum). Cancel or let existing
            orders reach a terminal state to free slots before placing new ones
            with distinct `clientId`s. |

            | `DUPLICATE_CLIENT_ID`              | Account already has an open
            order with the same `clientId`. |

            | `POSITION_TPSL_ALREADY_EXISTS`     | A position-level TPSL of the
            same trigger class (TP or SL) already exists for this
            account+market. At most one TP and one SL per market. |

            | `ENTRY_TPSL_CANNOT_BE_POSITION_TPSL` | A TPSL bound to an entry
            order (`parentOrderId` set) cannot also be a position-level TPSL;
            entry-linked TPSLs are sized partial legs. |

            | `OPEN_ORDER_CAP_EXCEEDED`          | Account has reached its
            per-account open-order cap; cancel a resting order or let one reach
            a terminal state before placing another. |

            | `FILL_WILL_EXCEED_TRADING_BOUND`   | Outside regular trading
            hours, the order would fill at or beyond the active off-hours
            trading bound. Per-order rejection; in-band trading continues. |

            | `OPEN_INTEREST_CAP_EXCEEDED`       | Market is at its operator-set
            open-interest cap and the order's next fill would have increased
            open interest. OI-neutral and OI-reducing orders still match. |

            | `ORDER_WILL_TAKE_LIQUIDITY_DURING_MARKET_HALT` | Deprecated — no
            longer emitted; replaced by `FILL_WILL_EXCEED_TRADING_BOUND`. |

            | `ORDER_NOT_FOUND`                  | `cancelOrder` referenced an
            order not on the orderbook (already filled, canceled, never placed,
            or owned by another account). |

            | `ORDER_NOT_FOUND_FOR_MODIFY`       | `modifyOrder` referenced an
            order not on the regular orderbook; TPSL and untriggered orders
            cannot be modified. Under cancel-replace modify semantics the
            rejected modify is also buffered briefly and applied if the target
            placement arrives shortly after. |

            | `MODIFY_CHANGED_IMMUTABLE_FIELD`   | `modifyOrder` attempted to
            change an immutable field (`side`, `timeInForce`, `reduceOnly`, or
            `orderType`). |

            | `MODIFY_ZERO_SIZE`                 | `modifyOrder` specified a new
            size of zero or less. |

            | `POSITION_SIZE_CAP_EXCEEDED`       | The order, modify, or
            resulting fill would exceed the per-market position notional cap.
            Distinct from `UNDERCOLLATERALIZED`: the account may be fully funded
            — the size is the problem. |

            | `MODIFY_TPSL_NOT_SUPPORTED`        | `modifyOrder` targeted an
            untriggered TPSL order; TPSL trigger parameters cannot be changed
            via modify. A live TPSL survives unchanged; a buffered modify that
            meets an arriving TPSL placement cancels that placement as a
            precaution. |

            | `MODIFY_SUPERSEDED_BY_CANCEL`      | `modifyOrder` was discarded
            because a `cancelOrder` for the same order is pending; cancels
            always dominate modifies and the order's final state is canceled. |

            | `MODIFY_SIZE_ALREADY_FILLED`       | Under total-size modify
            semantics, `modifyOrder`'s new size (the order's new TOTAL size) is
            at or below the quantity already filled, leaving nothing to rest.
            The original order is canceled (a `CANCELED` update precedes this
            rejection). |
          enum:
            - POST_ONLY_WOULD_CROSS
            - SELF_TRADE
            - UNDERCOLLATERALIZED
            - COULD_NOT_FILL
            - IOC_CANCELED
            - FOK_FAILED
            - REDUCE_ONLY_WOULD_INCREASE
            - TOO_MANY_CLIENT_IDS
            - DUPLICATE_CLIENT_ID
            - POSITION_TPSL_ALREADY_EXISTS
            - ENTRY_TPSL_CANNOT_BE_POSITION_TPSL
            - OPEN_ORDER_CAP_EXCEEDED
            - ORDER_WILL_TAKE_LIQUIDITY_DURING_MARKET_HALT
            - ORDER_NOT_FOUND
            - ORDER_NOT_FOUND_FOR_MODIFY
            - MODIFY_CHANGED_IMMUTABLE_FIELD
            - MODIFY_ZERO_SIZE
            - FILL_WILL_EXCEED_TRADING_BOUND
            - OPEN_INTEREST_CAP_EXCEEDED
            - POSITION_SIZE_CAP_EXCEEDED
            - MODIFY_TPSL_NOT_SUPPORTED
            - MODIFY_SUPERSEDED_BY_CANCEL
            - MODIFY_SIZE_ALREADY_FILLED
    EthereumAddressHex:
      type: string
      pattern: ^(0x|0X)?[0-9a-fA-F]{40}$
      description: >
        20-byte EVM address as hex: optional `0x` or `0X` prefix and exactly 40
        hexadecimal digits. API responses normalize to lowercase `a`–`f` after
        `0x`.
    ApiKeyAccountIndex:
      type: integer
      enum:
        - 0
        - 1
        - 2
        - 3
        - 4
        - 5
        - 6
        - 7
        - 8
        - 9
        - 255
      description: >
        Subaccount scope for an API key. Two forms are accepted:

        - `0`–`9`: the key may trade only that single subaccount.

        - `255`: the key is scoped to **all** of the address's subaccounts (the
        default for web/mobile trading keys), so one key trades any subaccount
        without a separate key per subaccount.


        The value is bound into the signed EIP-712 payload, so the wallet
        signature attests to exactly the scope granted — any non-zero value
        (including `255`) MUST use the `accountIndex`-bearing `CreateApiKey` /
        `RevokeApiKey` type. Values `10`–`254` are rejected.
    EthereumSignature:
      type: object
      description: >-
        secp256k1 ECDSA signature components `(r, s, v)` produced by the wallet
        that owns `address`. The signed digest is endpoint-specific: `withdraw`,
        `createApiKey`, and `revokeApiKey` sign **EIP-712 typed data**
        (`eth_signTypedData_v4`) — see each endpoint description for the exact
        domain, types, and message. The gateway recovers the signer with
        `ecrecover` and rejects requests where the recovered address does not
        match `address` (HTTP 401).
      required:
        - r
        - s
        - v
      properties:
        r:
          type: string
          pattern: ^(?=.*[1-9a-fA-F])(0x)?[0-9a-fA-F]{1,64}$
          minLength: 1
          maxLength: 66
          description: >-
            Hex-encoded r component of the secp256k1 signature (up to 32 bytes;
            leading zeros may be omitted, but the value must be non-zero).
          examples:
            - '0x69e7308419769592ec8363d002e3077e803e403b4e1920fc30e485b63d76e2a3'
        s:
          type: string
          pattern: ^(?=.*[1-9a-fA-F])(0x)?[0-9a-fA-F]{1,64}$
          minLength: 1
          maxLength: 66
          description: >-
            Hex-encoded s component of the secp256k1 signature (up to 32 bytes;
            leading zeros may be omitted, but the value must be non-zero).
          examples:
            - '0x0980d0742c9e65037af9d54e559704ae2c2285f3112a9d89544191a740f103cf'
        v:
          type: string
          pattern: ^(0x)?[0-9a-fA-F]{1,8}$
          minLength: 1
          maxLength: 10
          description: >-
            Hex-encoded recovery byte (`v`). Accepts any standard Ethereum
            encoding: raw recovery id (`0x00` / `0x01`), legacy (`0x1b` /
            `0x1c`), or EIP-155 chain-bound values; the gateway normalizes
            internally. Most wallet signing APIs (`eth_signTypedData_v4`,
            `personal_sign`) emit `0x1b`/`0x1c`.
          examples:
            - '0x1c'
    AccountIndex:
      type: integer
      minimum: 0
      maximum: 9
      description: >-
        Account index (account index, 0–9). Identifies the account for orders,
        positions, fills, and API keys.
    RateLimitedError:
      description: >
        429 response body shape. Extends the generic `Error` with two
        rate-limit-specific fields:


        - `reason` — which limiter rejected the request. Stable string enum; new
        values may be added in future versions, so clients should treat unknown
        values as opaque.

        - `retryAfterMs` — precise milliseconds the client should wait before
        retrying. The HTTP `Retry-After` header carries the same information
        rounded up to whole seconds (RFC 7231); prefer this field when
        sub-second precision matters.
      type: object
      required:
        - error
      properties:
        error:
          type: string
          example: rate limited
        reason:
          type: string
          enum:
            - ip
            - account_empty
            - account_partial
            - unknown
          description: |
            Layer that rejected the request:
              * `ip` — per-IP weight bucket exhausted.
              * `account_empty` — per-subaccount pool fully exhausted; the
                drip-throttle has no token available either.
              * `account_partial` — pool has some credit but not enough
                for this batch. Split the request into smaller chunks to
                drain the remaining headroom.
              * `unknown` — defensive fallback; clients should retry per
                `retryAfterMs` and report the occurrence.
        retryAfterMs:
          type: integer
          format: int64
          minimum: 0
          description: >-
            Milliseconds to wait before retrying. Matches the precise wait the
            rate limiter computed (the `Retry-After` header rounds up to the
            next whole second).
        clientId:
          type: string
          description: >
            Echo of the request's client-supplied `clientId`, so the client can
            correlate the rejection to a specific order request. Present only on
            single-order endpoints (placeOrder, modifyOrder, cancelOrder) when
            the request carried a `clientId`. Omitted on batch endpoints (see
            `clientIds`) and when the request had none.
        clientIds:
          type: array
          items:
            type: string
          description: >
            Echo of every client-supplied `clientId` in a batch request
            (batchPlaceOrders, batchCancelOrders), positionally aligned with the
            submitted `orders` / `cancels` array. A batch is rejected
            all-or-nothing, so every listed order was rejected; the echo lets a
            client submitting multiple batches concurrently correlate the 429 to
            a specific batch. An entry is empty when that element carried no
            `clientId` (e.g. a cancel-by-orderId). Omitted on single-order
            endpoints (see `clientId`).
  responses:
    TooManyRequests:
      description: >
        Rate limit exceeded. Two signals are returned, designed to coexist with
        both naive HTTP clients and rate-limit-aware SDK clients:


        - `Retry-After` header (integer seconds, RFC 7231 compliant). Rounds up
        — clients that obey this header sleep at least as long as required. Safe
        for generic HTTP libraries to read.

        - JSON body `retryAfterMs` (precise milliseconds, matches the server's
        internal computation). Sophisticated clients should prefer this over the
        header to avoid the round-up overshoot.

        - JSON body `reason` indicates which rate-limit layer rejected: `ip`
        (per-IP weight bucket), `account_empty` (per-subaccount pool fully
        exhausted, in drip throttle), `account_partial` (pool has some credit
        but not enough for this batch — split the request smaller to drain), or
        `unknown` (defensive fallback).

        - JSON body `clientId` (single-order endpoints) or `clientIds` (batch
        endpoints) echoes the request's client-supplied order identifier(s) so
        the client can correlate the rejection to a specific order request.
      headers:
        Retry-After:
          $ref: '#/components/headers/RetryAfter'
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/RateLimitedError'
          example:
            error: rate limited
            reason: account_empty
            retryAfterMs: 850
            clientId: my-order-42
  headers:
    RetryAfter:
      description: Seconds the client should wait before retrying the request.
      schema:
        type: integer
        minimum: 1
        example: 1

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.