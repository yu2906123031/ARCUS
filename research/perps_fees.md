> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Get fee tier table

> Returns the complete exchange-wide fee tier table, sorted ascending by level.

<Note>**The response body shown here is a static example, not live data.** After you click **Send**, your live result appears in a separate panel headed **200 OK**. The panel under the status-code tabs is a fixed sample from the spec — its field values (fees, prices, sizes, IDs, timestamps) are placeholders. Use **Send**, or call the endpoint, for current values.</Note>

Returns the complete exchange-wide fee tier table, sorted ascending by level. The response reflects the in-memory cache on the serving gateway instance, which is refreshed sub-second whenever an admin fee tier update is confirmed by the matching engine.

No authentication required.


## OpenAPI

````yaml /api-reference/openapi.yml get /v1/feetiers
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
  /v1/feetiers:
    get:
      tags:
        - Public
      summary: Get fee tier table
      description: >-
        Returns the complete exchange-wide fee tier table, sorted ascending by
        level.
      operationId: getFeeTiers
      responses:
        '200':
          description: Fee tier table.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/FeeTiersResponse'
              example:
                tiers:
                  - level: 0
                    name: Base
                    volume_threshold: 0
                    maker_fee_ppm: 200
                    taker_fee_ppm: 500
                  - level: 1
                    name: Bronze
                    volume_threshold: 100000000000
                    maker_fee_ppm: 150
                    taker_fee_ppm: 450
        '429':
          $ref: '#/components/responses/TooManyRequests'
      x-codeSamples:
        - lang: Shell
          label: curl
          source: |
            curl https://api.arcus.xyz/v1/feetiers
        - lang: Python
          label: requests
          source: |
            import requests
            r = requests.get("https://api.arcus.xyz/v1/feetiers")
            print(r.json())
        - lang: TypeScript
          label: fetch
          source: |
            const res = await fetch("https://api.arcus.xyz/v1/feetiers");
            console.log(await res.json());
components:
  schemas:
    FeeTiersResponse:
      type: object
      description: Exchange-wide fee tier table returned by GET /v1/feetiers.
      required:
        - tiers
      properties:
        tiers:
          type: array
          items:
            $ref: '#/components/schemas/FeeTierConfigRow'
          description: >
            Full fee tier table, sorted ascending by level. Sourced from the
            in-memory cache, refreshed sub-second when an admin fee tier update
            is confirmed.
    FeeTierConfigRow:
      type: object
      description: A single row in the exchange-wide fee tier table.
      properties:
        level:
          type: integer
          format: uint8
          description: >
            Tier level index (0=Base, 1=Bronze, 2=Silver, 3=Gold, 4=Platinum,
            5=VIP, 6=Max).
        name:
          type: string
          description: Human-readable tier name (e.g. "Base", "Gold").
        volume_threshold:
          type: integer
          format: int64
          description: >
            30-day notional volume threshold (in USD×1e9) required to reach this
            tier. Always 0 for the base tier.
        maker_fee_ppm:
          type: integer
          format: int32
          description: >
            Maker fee rate in ppm — parts-per-million of notional (e.g. 200 =
            0.02% = 2 bps). Negative values indicate a maker rebate.
        taker_fee_ppm:
          type: integer
          format: int32
          description: >
            Taker fee rate in ppm — parts-per-million of notional (e.g. 500 =
            0.05% = 5 bps). Always ≥ 0.
      required:
        - level
        - name
        - volume_threshold
        - maker_fee_ppm
        - taker_fee_ppm
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