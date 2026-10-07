> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Schedule cancel-all (dead man's switch)

> Arm, refresh, or disarm a per-subaccount dead man's switch.

<Note>**The response body shown here is a static example, not live data.** After you click **Send**, your live result appears in a separate panel headed **200 OK**. The panel under the status-code tabs is a fixed sample from the spec — its field values (fees, prices, sizes, IDs, timestamps) are placeholders. Use **Send**, or call the endpoint, for current values.</Note>

Arm, refresh, or disarm a per-subaccount dead man's switch. If the deadline passes without a refresh, the gateway fires `cancelAllOrders` for the subaccount (account-wide).

Keep the switch armed by posting a **new** `time` before the current deadline: each refresh is a full re-arm with an updated absolute epoch **microseconds** timestamp (typically `now + 60s`). Reusing a past deadline, or letting `time` elapse, does not extend the previous arm.

Pass `time` to arm or refresh. Omit `time` (or send null) to disarm. Minimum lead is 5 seconds; maximum lead is 5 minutes. Successful auto-fires are capped at 10 per UTC day per subaccount (429 when exceeded).


## OpenAPI

````yaml /api-reference/openapi.yml post /v1/scheduleCancel
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
  /v1/scheduleCancel:
    post:
      tags:
        - Exchange
      summary: Schedule cancel-all (dead man's switch)
      description: Arm, refresh, or disarm a per-subaccount dead man's switch.
      operationId: scheduleCancel
      parameters:
        - $ref: '#/components/parameters/AddressQuery'
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ScheduleCancelRequest'
            examples:
              arm:
                summary: Arm a 60s dead man's switch
                value:
                  address: '0x1234567890abcdef1234567890abcdef12345678'
                  accountIndex: 0
                  time: 1734567890123000
              refresh:
                summary: Refresh with a later deadline (keep the switch armed)
                description: >
                  Send before the previous `time` elapses. `time` must be a new
                  absolute deadline in the 5s–5min window — typically now + 60s.
                value:
                  address: '0x1234567890abcdef1234567890abcdef12345678'
                  accountIndex: 0
                  time: 1734567950123000
              disarm:
                summary: Disarm the switch
                value:
                  address: '0x1234567890abcdef1234567890abcdef12345678'
                  accountIndex: 0
      responses:
        '200':
          description: Deadline armed, refreshed, or disarmed.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ScheduleCancelResponse'
        '400':
          description: >
            Validation error. Structured rejections include `errorSource:
            Cancel` and `errorType: InvalidRequest`.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '401':
          description: Missing or invalid API key.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '403':
          description: Address or accountIndex does not match the API key.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '429':
          description: >
            Rate/quota limit reached — either the shared per-subaccount daily
            auto-fire trigger cap, or the per-wallet live-switch cap (the
            maximum number of schedule-cancel switches a wallet may hold armed
            at once, across all subaccounts and markets). Disarm a switch before
            arming a new one. Refreshing an already-armed switch is never
            rejected here.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '503':
          description: >
            Cancel path disabled, schedule-cancel store unavailable, or storage
            I/O failed — treat the switch as not armed and retry. Structured
            rejections include `errorSource: Cancel` and `errorType:
            Unavailable`.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
      security:
        - apiKey: []
          timestamp: []
          signature: []
        - {}
components:
  parameters:
    AddressQuery:
      name: address
      in: query
      required: true
      schema:
        $ref: '#/components/schemas/EthereumAddressHex'
      description: >
        Master Ethereum address for this API key (must match `address` from
        `POST /createApiKey` for the same key). Required on REST for
        account-scoped reads and for place/cancel. Invalid hex → 400; mismatch
        with key → 403.
  schemas:
    ScheduleCancelRequest:
      type: object
      required:
        - address
        - accountIndex
      description: >
        Arm, refresh, or disarm a dead-man cancel-all deadline. When `time` is
        present it must be an absolute epoch **microseconds** deadline between 5
        seconds and 5 minutes in the future (inclusive). Each refresh must send
        a **new** `time` (a later absolute deadline) before the current one
        fires; omitting `time` (or sending null) disarms.


        Omit `marketId` for an account-wide switch (fires cancel-all across
        every market), or set it to scope the switch to a single market. A
        per-market switch is independent of the account-wide switch and of other
        per-market switches on the same subaccount: each has its own deadline
        and fires only its own scope. `marketId` selects which switch to
        arm/refresh/disarm, so it must match between the arm and the matching
        disarm.


        A wallet may hold only a bounded number of switches armed at once
        (across all subaccounts and markets); arming a new switch beyond that
        limit returns `429`. Refreshing an already-armed switch is always
        allowed.
      properties:
        address:
          type: string
          description: >-
            Master Ethereum address for this API key (must match `?address=` if
            both are present).
        accountIndex:
          type: integer
          minimum: 0
          maximum: 9
        marketId:
          type: integer
          minimum: 0
          description: >
            Optional market scope. Omit for an account-wide switch; set to scope
            the switch to a single market. Independent of the account-wide
            switch and of other per-market switches on the same subaccount.
        time:
          type: integer
          format: int64
          description: >
            Absolute epoch microseconds when cancel-all fires if not refreshed.
            Each refresh must send a new later deadline (5s–5min ahead of now).
            Omit or null to disarm.
          nullable: true
    ScheduleCancelResponse:
      type: object
      required:
        - address
        - accountIndex
        - status
      properties:
        address:
          type: string
        accountIndex:
          type: integer
          minimum: 0
          maximum: 9
        marketId:
          type: integer
          minimum: 0
          description: >-
            Echoed back when the switch is scoped to one market; omitted for
            account-wide.
        marketDisplayName:
          type: string
          description: >-
            Human-readable market name (e.g. "BTC-USD") when the switch is
            per-market; omitted for account-wide.
        time:
          type: integer
          format: int64
          description: >-
            Echoed deadline in epoch microseconds on arm/refresh; omitted on
            disarm.
          nullable: true
        status:
          type: string
          enum:
            - scheduled
            - disarmed
        rateLimit:
          $ref: '#/components/schemas/AccountRateLimit'
          description: >
            Per-subaccount cancel-pool rate-limit snapshot after charging this
            ping. Omitted when rate limiting is not configured.
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
    AccountRateLimit:
      type: object
      required:
        - pool
        - remaining
      description: >
        Per-subaccount rate-limit snapshot for the pool charged by this request,
        attached to exchange write responses (placeOrder, modifyOrder,
        cancelOrder, batchPlaceOrders, batchCancelOrders, cancelAllOrders) on
        both REST and WebSocket. Reflects the account-pool state the limiter
        already computed while charging the request — no extra round-trip. The
        IP rate-limit layer is intentionally not exposed.
      properties:
        pool:
          type: string
          enum:
            - order
            - cancel
          description: >
            Which per-subaccount pool this request charged: `order` for
            place/modify/batchPlace, `cancel` for cancel/batchCancel/cancelAll.
        remaining:
          type: integer
          format: int64
          description: >
            Remaining tokens in the charged pool after this request (`floor(cap
            - consumed)`). Can be `0` or negative while the request still
            succeeds when the account is on the drip throttle (pool exhausted,
            one action allowed per drip period). A value of `-1` is a sentinel
            meaning the account layer was not enforced for this request.
          examples:
            - 9958
  securitySchemes:
    apiKey:
      type: apiKey
      in: header
      name: X-API-Key
      description: >
        Hex-encoded Ed25519 public key (64 chars). The public key IS the API key
        — register it via `POST /createApiKey`. Required on every authenticated
        request, both read-only and signed.
    timestamp:
      type: apiKey
      in: header
      name: X-Timestamp
      description: >
        Unix time in **nanoseconds** as a decimal string (e.g.
        `"1713825891591000000"`). Millisecond or second epochs are rejected with
        `401 Unauthorized`. Must be within ±30,000 ms of server wall-clock, or
        the request is rejected with `401 Unauthorized`. Required on all
        mutating / credential-creating endpoints. This same value must appear as
        the `ct` field in the ordersign typed canonical payload (single-order
        endpoints) or in each element's `ct` field (batch endpoints).
    signature:
      type: apiKey
      in: header
      name: X-Signature
      description: >
        Lowercase hex-encoded Ed25519 signature (128 chars).


        **Single-order endpoints** (`placeOrder`, `cancelOrder`, `modifyOrder`,
        and other non-batch mutating routes) sign over the **ordersign typed
        canonical payload** — a compact, key-sorted JSON object built from
        parsed request fields using engine-native integer values:


        ```

        placeOrder:  
        {"ad":"0x…","ai":N,[,"c":"…"],"ct":N,"g":N,"m":N,"op":1,"p":N,"q":N,"r":0|1,"s":N,"t":N,"v":1}

        cancelOrder: 
        {"ad":"0x…","ai":N,[,"c":"…"],"ct":N,[,"id":"…"],"m":N,"op":2,"v":1}

        modifyOrder: 
        {"ad":"0x…","ai":N,[,"c":"…"],"ct":N,"g":N,[,"id":"…"],"m":N,"op":3,"p":N,"q":N,"r":0|1,"s":N,"t":N,"v":1}  
        (exactly one of id / c)

        ```


        `ct` must equal the `X-Timestamp` header value. Keys in brackets are
        conditional (omitted when empty). `op` values: `1`=place, `2`=cancel,
        `3`=modify. See the `ordersign` package for field definitions and
        reference signing code.


        Other signed routes (e.g. `createApiKey`) still use the legacy scheme:
        `signing_message = X-Timestamp + ACTION + canonicalJSON(body)`, where
        `ACTION` is the camelCase final path segment.


        **Batch endpoints (`batchPlaceOrders`, `batchCancelOrders`,
        `batchModifyOrders`) do NOT use this header.** They authenticate with
        per-element typed ordersign signatures embedded in the request body (see
        the global auth description and the per-field `signature` descriptions
        on `OrderRequest` / `CancelOrderRequest` / `ModifyOrderRequest`).


        Read endpoints are authenticated by `?address=` (and optionally
        `X-API-Key`) only — no signature is required, except for the signed
        affiliate reads (see the Referral tag), which require the full header
        triple; with no body their signing message is `X-Timestamp + ACTION`.
        `canonicalJSON(body)` is the JSON body with object keys sorted
        lexicographically at every level and no whitespace; the server
        canonicalizes the received body before verifying, so only the bytes
        signed over must be canonical. Required on all mutating /
        credential-creating endpoints.

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.