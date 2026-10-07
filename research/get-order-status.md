> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Get order status

> Returns the status of a specific order by order ID. Requires `address` query parameter for the owning account because order data is partitioned by account, so the lookup needs the owning account.


<Note>**The response body shown here is a static example, not live data.** After you click **Send**, your live result appears in a separate panel headed **200 OK**. The panel under the status-code tabs is a fixed sample from the spec — its field values (fees, prices, sizes, IDs, timestamps) are placeholders. Use **Send**, or call the endpoint, for current values.</Note>

Returns the status of a specific order by order ID. Requires `address` query parameter for the owning account because order data is partitioned by account, so the lookup needs the owning account.


## OpenAPI

````yaml /api-reference/openapi.yml get /v1/order/{orderId}
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
  /v1/order/{orderId}:
    get:
      tags:
        - Public
      summary: Get order status
      description: >
        Returns the status of a specific order by order ID. Requires `address`
        query parameter for the owning account because order data is partitioned
        by account, so the lookup needs the owning account.
      operationId: getOrderStatus
      parameters:
        - name: orderId
          in: path
          required: true
          schema:
            type: string
          description: System-generated order ID (hex string).
        - $ref: '#/components/parameters/AddressQuery'
        - $ref: '#/components/parameters/AccountIndexQuery'
      responses:
        '200':
          description: Order status details.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/order'
        '400':
          description: Invalid orderId, or missing/invalid `address` query parameter.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '404':
          description: Order not found.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/error'
        '429':
          $ref: '#/components/responses/TooManyRequests'
      x-codeSamples:
        - lang: Shell
          label: curl
          source: |
            ADDR=0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb
            ORDER_ID=a1b2c3d4e5f6g7h8
            curl "https://api.arcus.xyz/v1/order/${ORDER_ID}?address=${ADDR}"
        - lang: Python
          label: requests
          source: |
            import requests
            orderId = "a1b2c3d4e5f6g7h8"
            r = requests.get(
                f"https://api.arcus.xyz/v1/order/{orderId}",
                params={"address": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb"},
            )
            print(r.json())
        - lang: TypeScript
          label: fetch
          source: >
            const orderId = "a1b2c3d4e5f6g7h8";

            const url = new URL(`https://api.arcus.xyz/v1/order/${orderId}`);

            url.searchParams.set("address",
            "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb");

            const res = await fetch(url);

            console.log(await res.json());
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
    AccountIndexQuery:
      name: accountIndex
      in: query
      required: false
      schema:
        type: integer
        minimum: 0
        maximum: 9
        default: 0
      description: >
        Subaccount index (0–9) to scope the request to. Defaults to 0 (the
        primary account). Values above 9 → 400.
  schemas:
    order:
      type: object
      description: >
        Unified order shape used across REST responses, WebSocket snapshots, and
        streaming updates. Fields only available from the store are optional and
        may be absent in streaming updates.
      required:
        - orderId
        - marketDisplayName
        - marketId
        - side
        - status
        - price
        - originalSize
        - remainingSize
        - updatedAt
      properties:
        orderId:
          type: string
          description: System-generated order ID.
        clientId:
          type: string
          maxLength: 36
          description: >
            Client-provided order identifier. Optional; max 36 characters. An
            account may have at most 10,000 live `clientId`s simultaneously —
            placing an order with a new `clientId` while at this limit is
            rejected with `TOO_MANY_CLIENT_IDS`. A `clientId` slot is freed when
            the order reaches a terminal state (`FILLED`, `CANCELED`, or
            `REJECTED`). Reusing an already-active `clientId` is rejected with
            `DUPLICATE_CLIENT_ID`.
        address:
          type: string
          pattern: ^(0x|0X)?[0-9a-fA-F]{40}$
          description: >
            Ethereum address of the account that owns the order. Present on
            persistence-sourced rows (REST `GET /orders`, `GET
            /order/{orderId}`, and the `orders` snapshot's
            `recentClosedOrders`); may be absent on streaming updates.
        accountIndex:
          type: integer
          minimum: 0
          maximum: 9
          description: Account index (0–9) of the owning account. Omitted when 0.
        marketId:
          type: integer
          minimum: 0
          maximum: 65535
          description: >-
            Perpetual market identifier (uint16). Map to display name via `GET
            /markets`. Used for orders, positions, funding, and market metadata.
        marketDisplayName:
          type: string
          description: Market symbol (e.g. BTC-USD).
          examples:
            - BTC-USD
        side:
          type: string
          enum:
            - BUY
            - SELL
          description: Order side.
        type:
          type: string
          enum:
            - LIMIT
            - MARKET
          description: >
            Execution style of the order. Use `tpslType` to mark an order as
            stop-loss or take-profit. Absent on forced closures (status
            `LIQUIDATED` or `ADL`): those have no originating user order, so no
            execution style exists — key off `status` instead.
        status:
          $ref: '#/components/schemas/order-status'
          description: Current status of an order.
        price:
          type: string
          description: Limit price in human-readable units (decimal string).
        originalSize:
          type: string
          description: Original order size in human-readable units (decimal string).
        filledSize:
          type: string
          description: >-
            Filled size in human-readable units (decimal string). Computed as
            originalSize − remainingSize.
        remainingSize:
          type: string
          description: >-
            Remaining unfilled size in human-readable units. "0" when fully
            filled.
        avgFillPrice:
          type: string
          description: >
            Cumulative average fill price (decimal string), computed as
            filledNotional / filledSize. Present on REST, snapshot, and
            streaming responses whenever the order has any filled quantity;
            absent when nothing has filled.
        timeInForce:
          $ref: '#/components/schemas/TimeInForceResponse'
          description: >
            Time-in-force policy for the order. Resting orders (submitted as
            GTT) are currently reported as `GTC` for backward compatibility with
            consumers that predate the GTT rename; treat `GTC` and `GTT` as
            equivalent when reading.
        goodTilTime:
          type: string
          description: >-
            Expiration timestamp in epoch microseconds (as string), the API's
            user-facing timestamp resolution. Present when the order carries a
            good-till-time expiry.
        reduceOnly:
          type: boolean
          description: >
            True when the order can only reduce an existing position (echoed
            from the placing request). Present only when true; absent for
            regular orders.
        triggerPrice:
          type: string
          description: Trigger price for stop/TPSL orders (decimal string).
        tpslType:
          type: string
          enum:
            - STOP_LOSS
            - TAKE_PROFIT
          description: >-
            Trigger purpose of the order, orthogonal to `type` (execution
            style). Absent for plain orders.
        isPositionTPSL:
          type: boolean
          description: >
            True when the TPSL closes the user's full open position at trigger
            time (resized against the live position) rather than a sized leg
            (partialTpsl) or an entryTpsl child. Only present on TPSL orders;
            absent on plain orders. At most one position-level TPSL of each
            trigger class (TP, SL) may be active per account+market — a second
            placement is rejected with `POSITION_TPSL_ALREADY_EXISTS`.
        parentOrderId:
          type: string
          description: >
            Entry order id this TPSL leg is bound to (entryTpsl bundles).
            Cancelling the entry cancels every TPSL leg pointing at it. Only
            present on TPSL orders that were placed alongside an entry; absent
            on standalone TPSLs (partialTpsl, positionTpsl) and on plain orders.
        rejectionReason:
          $ref: '#/components/schemas/RejectionReason'
          description: >-
            Machine-readable reason emitted by the matching engine when an order
            is rejected. See the canonical `RejectionReason` schema for the full
            enum and per-value descriptions.
        cancelReason:
          type: string
          enum:
            - MODIFY_CANCELED
          description: >
            Qualifies a CANCELED update when the matching engine stamped a
            reason. Currently the only value is MODIFY_CANCELED: this cancel is
            the first leg of a modify — the SAME order id is re-placed by the
            account's very next update (PLACED / FILL / REJECTED), so the order
            is not terminally gone unless that follow-up says so. Absent on all
            other cancels, which are genuinely terminal.
        state:
          type: string
          enum:
            - OPEN
            - PARTIALLY_FILLED
            - FILLED
            - CANCELED
            - REJECTED
          description: >
            Engine-authoritative lifecycle position after this event. Sourced
            directly from the matching engine and stamped on every streaming
            update — clients can drive an order state machine off this alone
            without parsing event types or comparing sizes:
              - Partial fill of a resting order: state=PARTIALLY_FILLED
                (status is "OPEN").
              - IOC partial: state=PARTIALLY_FILLED is terminal; the implicit
                cancel of the IOC remainder is conveyed by `timeInForce == IOC`
                with no follow-up event (status is "CANCELED" instead).
              - Explicit cancel after prior partial fills: state=CANCELED. `status` carries broader signals (ACK, CANCEL_PENDING, MARGIN_CANCELED, TPSL_*, etc.) that have no equivalent here.
        positionEffect:
          type: string
          enum:
            - OPEN_LONG
            - OPEN_SHORT
            - ADD_LONG
            - ADD_SHORT
            - CLOSE_LONG
            - CLOSE_SHORT
            - FLIP_LONG_TO_SHORT
            - FLIP_SHORT_TO_LONG
          description: >
            How a fill on this order changed the account's position in
            `marketId`. Only set on fill events; absent on placements / cancels
            / rejects. For taker fills the value is the net effect across all
            fills in the same aggregated update (FLIP_* covers a position that
            closed and reopened on the opposite side within one user order).
        createdAt:
          type: integer
          format: int64
          description: >-
            Order creation timestamp (epoch microseconds). Only present on REST
            and snapshot responses.
        updatedAt:
          type: integer
          format: int64
          description: Last update timestamp (epoch microseconds).
        sequenceNumber:
          type: integer
          format: uint64
          description: >
            Per-account sequence of the engine event that last wrote this order
            (same counter as `AccountUpdate.sequenceNumber`). Present on
            streaming `orders` channel updates and on WebSocket `OrdersSnapshot`
            rows; omitted on REST when unset.
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
    order-status:
      type: string
      description: Current status of an order.
      enum:
        - PENDING
        - OPEN
        - PARTIALLY_FILLED
        - FILLED
        - CANCELED
        - MARGIN_CANCELED
        - REJECTED
        - UNTRIGGERED
        - TPSL_PLACED
        - TPSL_TRIGGERED
        - TPSL_CANCELED
        - LIQUIDATED
        - ADL
        - ACK
        - CANCEL_ACKNOWLEDGED
        - CANCEL_ALL_ACKNOWLEDGED
        - CANCEL_PENDING
        - ERROR
    TimeInForceResponse:
      type: string
      enum:
        - GTC
        - GTT
        - IOC
        - FOK
        - ALO
      description: >
        Time-in-force as reported on order reads (REST order queries and the
        `orders` WebSocket channel). `GTC` is the legacy name for the resting
        bucket and is what resting orders (submitted as `GTT`) are currently
        reported as, for backward compatibility with consumers that predate the
        GTT rename. Treat `GTC` and `GTT` as equivalent when reading.
    RejectionReason:
      type: string
      description: >
        Machine-readable reason emitted by the matching engine when an order is
        rejected. Delivered on `AccountUpdate` events with `type: REJECTED` over
        the WebSocket `account` channel, and (best-effort) on `OrderResponse` /
        `CancelOrderResponse` HTTP bodies that resolved to definitive state
        before returning.


        | Value                       |
        Meaning                                                                
        |

        |-----------------------------|-------------------------------------------------------------------------|

        | `POST_ONLY_WOULD_CROSS`     | A post-only order was rejected because
        it would have crossed the book and taken liquidity. |

        | `SELF_TRADE`                | The order would have matched against
        another resting order belonging to the same account; self-trade
        prevention rejected it. |

        | `UNDERCOLLATERALIZED`       | Account has insufficient free collateral
        / margin to support the order.  |

        | `COULD_NOT_FILL`            | Generic "could not fill" outcome. Legacy
        reason — prefer the IOC/FOK-specific values below for
        time-in-force-driven cancels. |

        | `IOC_CANCELED`              | An `IMMEDIATE_OR_CANCEL` order produced
        zero fills against the book and was canceled. |

        | `FOK_FAILED`                | A `FILL_OR_KILL` order could not be
        filled in full at submission and was canceled in its entirety. |

        | `REDUCE_ONLY_WOULD_INCREASE`| A `reduceOnly` order was rejected
        because executing it would have opened or increased the account's
        position rather than reducing it. |

        | `TOO_MANY_CLIENT_IDS`       | Account already has 10,000 live
        `clientId`s (the per-account maximum). Cancel an existing order, or wait
        for one to reach a terminal state (`FILLED`, `CANCELED`, or `REJECTED`),
        to free a slot before placing an order with a new `clientId`. |

        | `DUPLICATE_CLIENT_ID`       | A `placeOrder` was rejected because the
        account already has an open order with the same `clientId`. The clientId
        becomes reusable once the prior order reaches a terminal state
        (`FILLED`, `CANCELED`, or `REJECTED`), or you can change the existing
        order in place via `modifyOrder`. |

        | `OPEN_ORDER_CAP_EXCEEDED`   | A `placeOrder` was rejected because the
        account already holds the maximum number of simultaneously open orders.
        Cancel an existing order, or wait for one to reach a terminal state
        (`FILLED`, `CANCELED`, or `REJECTED`), before placing another. No
        account state changes. |

        | `POSITION_TPSL_ALREADY_EXISTS` | A position-level TPSL of the same
        trigger class (TP or SL) already exists for this account+market; at most
        one TP and one SL position-level TPSL is allowed per market. |

        | `ENTRY_TPSL_CANNOT_BE_POSITION_TPSL` | A TPSL linked to an entry order
        (`parentOrderId` set) cannot also be a position-level TPSL. Entry-linked
        TPSLs are sized partial legs and must not carry position-level
        semantics. |

        | `FILL_WILL_EXCEED_TRADING_BOUND` | Outside regular trading hours
        (RTH), the order would fill at or beyond the active off-hours trading
        bound and was rejected. Applies to crossing takers, crossing
        `modifyOrder` cancel-replaces (the original order is removed), triggered
        TPSLs, and placements that would rest at a price a taker could reach
        beyond the band. The rejection is per-order — other trading inside the
        band continues — and the bound widens only after sustained resting
        liquidity near the bound (or the market re-enters RTH). |

        | `ORDER_WILL_TAKE_LIQUIDITY_DURING_MARKET_HALT` | **Deprecated** — no
        longer emitted; replaced by `FILL_WILL_EXCEED_TRADING_BOUND`. Seen only
        on historical orders. |

        | `ORDER_NOT_FOUND`           | A `cancelOrder` (or a
        `batchCancelOrders` element) matched no live order for the account — the
        target was already filled, already canceled, never existed, or the
        `clientId` does not resolve under the account. No account state changes.
        (Modify has its own reason, `ORDER_NOT_FOUND_FOR_MODIFY`.) |

        | `ORDER_NOT_FOUND_FOR_MODIFY` | A `modifyOrder` targeted an order that
        is not on the regular orderbook — missing, already filled, canceled,
        owned by another account, or a `clientId` that does not resolve under
        the account. TPSL and untriggered orders cannot be modified (see
        `MODIFY_TPSL_NOT_SUPPORTED`). When cancel-replace modify semantics are
        active, the rejected modify is also buffered briefly: if the target
        order's placement arrives shortly after, the placement is canceled
        (`cancelReason: MODIFY_CANCELED`) and replaced with the modify's
        parameters, mirroring buffered-cancel behavior. |

        | `MODIFY_CHANGED_IMMUTABLE_FIELD` | A `modifyOrder` attempted to change
        an immutable field (`side`, `timeInForce`, `reduceOnly`, or
        `orderType`). Only price and size may be modified; place a new order to
        change anything else. |

        | `MODIFY_ZERO_SIZE` | A `modifyOrder` specified a new size of zero or
        less. Use `cancelOrder` to remove an order rather than modifying it to
        zero. |

        | `PRICE_WILL_EXCEED_MAXIMUM_OUTSIDE_RTH_TRADING_BOUND` | **Deprecated**
        — no longer emitted; replaced by `FILL_WILL_EXCEED_TRADING_BOUND`. Seen
        only on historical orders. |

        | `MODIFY_WOULD_CROSS_OUTSIDE_RTH_TRADING_BOUNDARY` | A non-crossing
        `modifyOrder` reprice was rejected because the new price would violate
        the active off-hours trading band. Unlike
        `FILL_WILL_EXCEED_TRADING_BOUND` (used on the crossing cancel-replace
        path, which removes the original), this reason means the in-place modify
        was rejected and the **original order is still resting** on the book
        unchanged. |

        | `OPEN_INTEREST_CAP_EXCEEDED` | The market is at its operator-set
        open-interest cap (see `MarketInfo.openInterestCapNotional`) and this
        order's next fill would have increased open interest, so matching halted
        and the order (or its unfilled remainder) was rejected. Fills already
        executed before the halt stand. OI-neutral and OI-reducing orders —
        closes, and opener-vs-closer pairings — still match; the cap clears as
        open interest falls or the operator raises it. |

        | `POSITION_SIZE_CAP_EXCEEDED` | The order, modify, or resulting fill
        would exceed the per-market position notional cap. Distinct from
        `UNDERCOLLATERALIZED`: the account may be fully funded — the size is the
        problem. |

        | `MODIFY_TPSL_NOT_SUPPORTED` | A `modifyOrder` targeted an untriggered
        TPSL order. TPSL trigger parameters cannot be changed via `modifyOrder`;
        cancel the TPSL and place a new one instead. If the TPSL was live on the
        untriggered book, it survives unchanged. If the modify was buffered
        (arrived before the TPSL placement), the TPSL placement is canceled as a
        precaution and this rejection is emitted for the modify. |

        | `MODIFY_SUPERSEDED_BY_CANCEL` | A `modifyOrder` was discarded because
        a `cancelOrder` for the same order is pending. Cancels always dominate
        modifies: whether the cancel was already buffered when the modify
        arrived, or arrived after the modify was buffered, the modify is
        rejected with this reason and the order's final state is canceled. |

        | `MODIFY_SIZE_ALREADY_FILLED` | Under total-size modify semantics, the
        `modifyOrder`'s new size — the order's new TOTAL size, filled quantity
        included — is at or below the quantity already filled, leaving nothing
        to rest. The original order is canceled (a `CANCELED` update precedes
        this rejection); use `cancelOrder` when the intent is to stop the
        unfilled remainder. |

        | `EXPIRED`                   | A resting good-til-time order was
        removed because its `goodTilTime` had elapsed. The order never filled
        and is reported as `REJECTED` to its owner. No account state changes. |
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
        - ORDER_WILL_TAKE_LIQUIDITY_DURING_MARKET_HALT
        - ORDER_NOT_FOUND_FOR_MODIFY
        - MODIFY_CHANGED_IMMUTABLE_FIELD
        - MODIFY_ZERO_SIZE
        - PRICE_WILL_EXCEED_MAXIMUM_OUTSIDE_RTH_TRADING_BOUND
        - MODIFY_WOULD_CROSS_OUTSIDE_RTH_TRADING_BOUNDARY
        - FILL_WILL_EXCEED_TRADING_BOUND
        - OPEN_INTEREST_CAP_EXCEEDED
        - POSITION_SIZE_CAP_EXCEEDED
        - MODIFY_TPSL_NOT_SUPPORTED
        - MODIFY_SUPERSEDED_BY_CANCEL
        - MODIFY_SIZE_ALREADY_FILLED
        - ORDER_NOT_FOUND
        - OPEN_ORDER_CAP_EXCEEDED
        - EXPIRED
      x-enum-descriptions:
        - Post-only order would have crossed the book and taken liquidity.
        - >-
          Order would have matched against the account's own resting order;
          blocked by self-trade prevention.
        - Insufficient free collateral / margin to support the order.
        - >-
          Generic "could not fill" outcome. Legacy — prefer IOC_CANCELED /
          FOK_FAILED for time-in-force cancels.
        - IMMEDIATE_OR_CANCEL order produced zero fills and was canceled.
        - >-
          FILL_OR_KILL order could not be fully filled at submission and was
          canceled.
        - >-
          reduceOnly order would have opened or increased a position instead of
          reducing it.
        - >-
          Account already has 10,000 live clientIds (per-account maximum);
          cancel an existing order or let one reach a terminal state to free a
          slot.
        - >-
          Account already has an open order with the same clientId; reusable
          after the prior order terminates, or change the existing order in
          place via modifyOrder.
        - >-
          A position-level TPSL of the same trigger class (TP or SL) already
          exists for this account+market; at most one TP and one SL
          position-level TPSL is allowed per market.
        - >-
          A TPSL linked to an entry order (parentOrderId set) cannot also be a
          position-level TPSL; entry-linked TPSLs are sized partial legs.
        - >-
          Deprecated — no longer emitted; replaced by
          FILL_WILL_EXCEED_TRADING_BOUND. Seen only on historical orders.
        - >-
          modifyOrder targeted an order not on the regular orderbook (missing,
          filled, canceled, owned by another account, or a clientId that does
          not resolve under the account); TPSL and untriggered orders cannot be
          modified. Under cancel-replace modify semantics the rejected modify is
          also buffered briefly and applied if the target placement arrives
          shortly after.
        - >-
          modifyOrder attempted to change an immutable field (side, timeInForce,
          reduceOnly, or orderType); only price and size may be modified.
        - >-
          modifyOrder specified a new size of zero or less; use cancelOrder
          instead of modifying to zero.
        - >-
          Deprecated — no longer emitted; replaced by
          FILL_WILL_EXCEED_TRADING_BOUND. Seen only on historical orders.
        - >-
          Non-crossing modify reprice rejected because the new price violates
          the active off-hours trading band; unlike
          FILL_WILL_EXCEED_TRADING_BOUND (crossing path), the original order is
          still resting on the book unchanged.
        - >-
          Outside regular trading hours, the order would fill at or beyond the
          active off-hours trading bound; per-order rejection covering crossing
          takers, crossing modify cancel-replaces, triggered TPSLs, and
          dangerous placements. In-band trading continues; the bound widens only
          via sustained resting liquidity or RTH re-entry.
        - >-
          Market is at its operator-set open-interest cap and the order's next
          fill would have increased open interest; matching halted and the order
          (or its unfilled remainder) was rejected. OI-neutral and OI-reducing
          orders still match.
        - >-
          The order, modify, or resulting fill would exceed the per-market
          position notional cap. Distinct from UNDERCOLLATERALIZED — the account
          may be fully funded; the size is the problem.
        - >-
          modifyOrder targeted an untriggered TPSL order; TPSL trigger
          parameters cannot be changed via modifyOrder. A live TPSL survives
          unchanged; a buffered modify that meets an arriving TPSL placement
          cancels that placement as a precaution.
        - >-
          modifyOrder was discarded because a cancelOrder for the same order is
          pending; cancels always dominate modifies and the order's final state
          is canceled.
        - >-
          Under total-size modify semantics, modifyOrder's new size (the order's
          new TOTAL size, filled quantity included) is at or below the quantity
          already filled, leaving nothing to rest; the original order is
          canceled and this rejection follows.
        - >-
          cancelOrder/batchCancelOrders matched no live order for the account
          (already filled, canceled, never existed, or an unrecognised
          clientId); no account state change. Modify uses
          ORDER_NOT_FOUND_FOR_MODIFY instead.
        - >-
          placeOrder rejected because the account is already at its simultaneous
          open-order maximum; cancel an order or let one reach a terminal state
          to free a slot. No account state change.
        - >-
          Resting good-til-time order removed because its goodTilTime had
          elapsed; never filled, reported REJECTED to the owner. No account
          state change.
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