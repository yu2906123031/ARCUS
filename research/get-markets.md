> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Get markets

> Returns all markets with details, sorted by ascending `marketId`. Optionally filter to a single market.


<Note>**The response body shown here is a static example, not live data.** After you click **Send**, your live result appears in a separate panel headed **200 OK**. The panel under the status-code tabs is a fixed sample from the spec — its field values (fees, prices, sizes, IDs, timestamps) are placeholders. Use **Send**, or call the endpoint, for current values.</Note>

Returns all markets with details, sorted by ascending `marketId`. Optionally filter to a single market.


## OpenAPI

````yaml /api-reference/openapi.yml get /v1/markets
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
  /v1/markets:
    get:
      tags:
        - Public
      summary: Get markets
      description: >
        Returns all markets with details, sorted by ascending `marketId`.
        Optionally filter to a single market.
      operationId: getMarkets
      parameters:
        - name: market
          in: query
          required: false
          schema:
            type: string
            example: BTC-USD
          description: >
            Restrict results to a single market. Accepts either the display name
            (e.g. `BTC-USD`, case-insensitive) or the numeric market id (e.g.
            `1`). Omit to return every market. An unresolvable value → 400.
          examples:
            btc:
              value: BTC-USD
      responses:
        '200':
          description: Markets list.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/MarketsResponse'
        '400':
          description: Invalid market filter.
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
            curl https://api.arcus.xyz/v1/markets
            curl "https://api.arcus.xyz/v1/markets?market=BTC-USD"
        - lang: Python
          label: requests
          source: |
            import requests
            r = requests.get(
                "https://api.arcus.xyz/v1/markets",
                params={"market": "BTC-USD"},
            )
            print(r.json())
        - lang: TypeScript
          label: fetch
          source: |
            const url = new URL("https://api.arcus.xyz/v1/markets");
            url.searchParams.set("market", "BTC-USD");
            const res = await fetch(url);
            console.log(await res.json());
components:
  schemas:
    MarketsResponse:
      type: object
      required:
        - markets
      properties:
        markets:
          type: array
          description: One entry per perpetual market, sorted by ascending `marketId`.
          items:
            $ref: '#/components/schemas/MarketInfo'
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
    MarketInfo:
      type: object
      required:
        - marketDisplayName
        - marketId
        - status
        - baseAsset
        - quoteAsset
        - tickSize
        - stepSize
        - tickTiers
        - minOrderNotional
        - minOrderSize
        - maxOrderSize
        - type
        - category
        - addedTimestamp
        - regularTradingHours
        - isOutsideRth
        - currentSettlementPrice
        - upperTradingBound
        - lowerTradingBound
        - nextUpperTradingBound
        - nextLowerTradingBound
        - isUpperInExpansionZone
        - isLowerInExpansionZone
        - upperZoneEnteredAt
        - upperExpectedExpansionAt
        - lowerZoneEnteredAt
        - lowerExpectedExpansionAt
      properties:
        marketDisplayName:
          $ref: '#/components/schemas/MarketDisplayName'
        fullAssetName:
          type: string
          description: >-
            Human-readable name of the underlying asset. Omitted when no
            canonical name is configured for the market.
          examples:
            - Bitcoin
        marketId:
          $ref: '#/components/schemas/MarketId'
        status:
          type: string
          description: >-
            Operational state of the market. `OFFLINE` markets are returned for
            visibility but should not be quoted or traded.
          examples:
            - ONLINE
            - OFFLINE
        baseAsset:
          type: string
          examples:
            - BTC
        quoteAsset:
          type: string
          examples:
            - USD
        tickSize:
          type: string
        stepSize:
          type: string
        tickTiers:
          type: array
          description: >
            Per-price-band placement grid: a submitted limit price must be a
            multiple of the `tick` for the band its price falls in. Bands are
            ascending by `upToPrice` (the last band is unbounded — `upToPrice`
            omitted). `tickSize` equals the base tier, so clients that read only
            `tickSize` stay correct for prices in the base band.
          items:
            type: object
            required:
              - tick
            properties:
              upToPrice:
                type: string
                description: >-
                  Exclusive upper price bound (decimal USD) for this band;
                  omitted for the unbounded top band.
              tick:
                type: string
                description: Minimum price increment (decimal USD) for prices in this band.
        minOrderNotional:
          type: string
          description: >
            Flat minimum order notional (`quantity` × `price`, decimal USD) on
            position-opening orders. Reduce-only orders (including TPSLs) are
            exempt.
          examples:
            - '5'
        minOrderSize:
          type: string
          description: Minimum order quantity, in base-asset units.
          examples:
            - '0.0001'
        maxOrderSize:
          type: string
          description: >
            Maximum order quantity, in base-asset units. A per-order security
            gate rather than a dust filter — reduce-only orders are NOT exempt.
          examples:
            - '10000'
        oraclePrice:
          type: string
          description: >-
            Latest oracle price as a decimal USD string. "0" if no update has
            been received yet.
        markPrice:
          type: string
          description: >
            Authoritative mark price stamped by the matching engine, as a
            decimal USD string. "0" means no mark price has been received yet —
            callers must not fall back to `oraclePrice` in that case. Currently
            equals `oraclePrice`.
        lastTradePrice:
          type: string
          description: >
            Price of the most recently matched trade, as a decimal USD string.
            Omitted when no trade has been observed since the service started.
          examples:
            - '83146.8'
        fundingRate:
          type: string
          description: >
            Most recently applied funding rate — always the rate the engine
            actually charged at the last tick, never a recomputation. Funding
            keeps ticking hourly while a market is outside its
            `regularTradingHours` window, with each off-hours tick pinned to the
            fixed financing rate (SOFR + 0.5% annualized, expressed per hour),
            so this converges to the off-hours rate within one funding interval
            of a close.
        nextFundingRate:
          type: string
          description: >
            Forecast for the next funding rate. In regular trading hours (and
            for 24/7 markets) this is the funder's in-progress order-book
            forecast; while the market is outside RTH (`isOutsideRth` is `true`)
            it is the fixed off-hours financing rate (SOFR + 0.5% annualized,
            per hour) the engine will pin the next tick to.
        nextFundingAt:
          type: integer
          format: int64
          description: Unix seconds for the next funding payment time.
        priceChange24h:
          type: string
          description: >-
            Fractional change in the mark price (not the oracle price) over the
            trailing 24h (e.g. "0.025" = +2.5%), computed from a rolling
            per-market ring of the first mark price seen each minute.
        volume24h:
          type: string
        volume24hNotional:
          type: string
        high24h:
          type: string
          description: >
            Highest transaction price over the trailing 24h rolling window, from
            closed 1m candles with at least one trade. Omitted when no trades
            occurred in the window.
        low24h:
          type: string
          description: >
            Lowest transaction price over the trailing 24h rolling window, from
            closed 1m candles with at least one trade. Omitted when no trades
            occurred in the window.
        trades24h:
          type: integer
          format: int64
        openInterest:
          type: string
          description: >-
            Open interest: total size of all open long positions in the market
            (equals total short), in base asset units. Refreshed periodically
            from a snapshot, so it may lag real time by up to the poll interval.
        openInterestCapNotional:
          type: string
          description: >
            Operator-set maximum open interest for the market as a USD-notional
            decimal string (`openInterest` × mark price). While the market is at
            its cap, the engine rejects OI-increasing fills with rejection
            reason `OPEN_INTEREST_CAP_EXCEEDED`; OI-neutral and OI-reducing
            trades still match. `null` when no cap is active (formerly
            `openInterestCap`). Refreshed periodically from a snapshot, so a
            recent operator change may lag here by up to the poll interval.
          examples:
            - '50000000'
          nullable: true
        initialMarginFraction:
          type: string
        maintenanceMarginFraction:
          type: string
        offHoursInitialMarginFraction:
          type: string
          description: >
            Initial margin fraction the engine enforces when the market is
            outside its `regularTradingHours` window (overnight and weekends for
            equity-class markets), reflecting gap risk while the venue is
            closed. Equals `initialMarginFraction` for 24/7 markets (crypto).
            Size buying power against this whenever `regularTradingHours` is
            present and the current time falls outside that window — otherwise
            an order sized at the in-RTH leverage will be rejected
            `UNDERCOLLATERALIZED`. Maintenance margin is not uplifted off-hours.
        regularTradingHours:
          anyOf:
            - $ref: '#/components/schemas/RegularTradingHours'
            - type: 'null'
          description: >
            Session window during which the base initial margin fraction
            applies. Always present: `null` for 24/7 markets (crypto), which
            have no session boundary.
        isOutsideRth:
          type: boolean
          description: >
            Off-hours flag, sourced from the exchange calendar (weekends and US
            holidays included) rather than derived client-side. When `true`,
            `nextFundingRate` carries the fixed off-hours rate (SOFR + 0.5%).
            Always present: `false` for 24/7 markets (crypto), which never leave
            regular trading hours; `null` briefly while the server has not yet
            learned an RTH market's state (e.g. right after a deploy).
          nullable: true
        currentSettlementPrice:
          type: string
          description: >
            Sealed VWAP settlement price (decimal USD) that anchors the
            off-hours trading band, sealed by the engine when the market exits
            its `regularTradingHours` window. Always present: non-null only
            while the market is outside RTH with a sealed anchor; `null` in-RTH
            and for 24/7 markets (crypto), which have no settlement anchor.
          nullable: true
        upperTradingBound:
          type: string
          description: >
            Actual upper price boundary (decimal USD) of the off-hours trading
            band the engine enforces: `currentSettlementPrice + anchor ×
            initialMarginFraction × bandMultiplier`, where the band multiplier
            walks the fixed `0.5×` → `1×` → `2×` → `4×` ladder independently per
            side. Fills at or above this price are halted at the bound. Always
            present: non-null only while the market is outside RTH with a sealed
            anchor; `null` in-RTH and for 24/7 markets.
          nullable: true
        lowerTradingBound:
          type: string
          description: >
            Actual lower price boundary (decimal USD) of the off-hours trading
            band. Fills at or below this price are halted at the bound. See
            `upperTradingBound`. Always present: non-null only while the market
            is outside RTH with a sealed anchor; `null` in-RTH and for 24/7
            markets.
          nullable: true
        nextUpperTradingBound:
          type: string
          description: >
            The price (decimal USD) the upper boundary moves to if the upper
            band expands one level on the `0.5×` → `1×` → `2×` → `4×` ladder.
            Equals `upperTradingBound` at the `4×` ceiling (no wider band).
            Always present: non-null only while the market is outside RTH with a
            sealed anchor; `null` in-RTH and for 24/7 markets.
          nullable: true
        nextLowerTradingBound:
          type: string
          description: >
            The price (decimal USD) the lower boundary moves to if the lower
            band expands one level. Equals `lowerTradingBound` at the `4×`
            ceiling. Always present: non-null only while the market is outside
            RTH with a sealed anchor; `null` in-RTH and for 24/7 markets.
          nullable: true
        isUpperInExpansionZone:
          type: boolean
          description: >
            Whether the upper band side is in its expansion zone: the 1-hour
            EWMA of the bid-side impact price is holding within 90% of the upper
            trading boundary. If it holds there for one continuous hour, the
            upper band expands one level on the `0.5×` → `1×` → `2×` → `4×`
            ladder. The upper and lower zones are independent, so both can be
            `true` at once. Always present: non-null only while the market is
            outside RTH with a sealed anchor; `null` in-RTH and for 24/7
            markets.
          nullable: true
        isLowerInExpansionZone:
          type: boolean
          description: >
            Whether the lower band side is in its expansion zone (the 1-hour
            EWMA of the ask-side impact price is holding within 90% of the lower
            trading boundary). See `isUpperInExpansionZone`. Always present:
            non-null only while the market is outside RTH with a sealed anchor;
            `null` otherwise.
          nullable: true
        upperZoneEnteredAt:
          type: integer
          format: int64
          description: >
            Unix seconds at which the upper side entered its expansion zone.
            Constant while the side stays in the zone. Always present: non-null
            only while `isUpperInExpansionZone` is `true`.
          nullable: true
        upperExpectedExpansionAt:
          type: integer
          format: int64
          description: >
            Unix seconds of the EARLIEST time the upper bound can expand —
            exactly one hour after `upperZoneEnteredAt`. Not a guarantee: the
            EWMA must still be in the zone with sufficient resting depth when
            the hour completes, and the zone can be exited (nulling this field)
            at any time. Always present: non-null only while
            `isUpperInExpansionZone` is `true`.
          nullable: true
        lowerZoneEnteredAt:
          type: integer
          format: int64
          description: >
            Unix seconds at which the lower side entered its expansion zone. See
            `upperZoneEnteredAt`. Always present: non-null only while
            `isLowerInExpansionZone` is `true`.
          nullable: true
        lowerExpectedExpansionAt:
          type: integer
          format: int64
          description: >
            Unix seconds of the earliest time the lower bound can expand. See
            `upperExpectedExpansionAt`. Always present: non-null only while
            `isLowerInExpansionZone` is `true`.
          nullable: true
        type:
          $ref: '#/components/schemas/MarketType'
        category:
          $ref: '#/components/schemas/MarketCategory'
        addedTimestamp:
          type: integer
          format: int64
          description: Unix seconds at which this market was first listed.
        assetResolution:
          type: string
          description: >-
            Integer scaling factor used for on-wire quantum math (e.g.
            `"10000000000"` for 1e-10 base units).
        pythId:
          type: string
          description: >-
            Pyth Network price feed id for this market's index/oracle pair —
            numeric string for Pyth Pro, 0x-prefixed hex for legacy Hermes (e.g.
            `"1"` for Crypto.BTC/USD on Pyth Pro).
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
    MarketDisplayName:
      type: string
      description: Market symbol (e.g. BTC-USD).
      examples:
        - BTC-USD
    MarketId:
      type: integer
      minimum: 0
      maximum: 65535
      description: >-
        Perpetual market identifier (uint16). Map to display name via `GET
        /markets`. Used for orders, positions, funding, and market metadata.
    RegularTradingHours:
      type: object
      description: >
        Market session window during which the base margin fractions
        (`initialMarginFraction` / `maintenanceMarginFraction`) apply. Outside
        this window the `offHours*` fractions apply. Times are local
        seconds-of-day in [0, 86400) in `timezone`. Weekend and holiday closures
        are venue-calendar concerns layered on top and are NOT encoded here, so
        a client deriving the current session must also exclude those. `null`
        for 24/7 markets (crypto).
      required:
        - startSecondsOfDay
        - endSecondsOfDay
        - timezone
        - isOvernight
      properties:
        startSecondsOfDay:
          type: integer
          format: int32
          description: >-
            Local seconds-of-day at which the session opens (e.g. `14400` =
            04:00).
        endSecondsOfDay:
          type: integer
          format: int32
          description: >-
            Local seconds-of-day at which the session closes. Half-open — a time
            exactly at the close is outside RTH.
        timezone:
          type: string
          description: IANA timezone the seconds-of-day are expressed in.
          examples:
            - America/New_York
        isOvernight:
          type: boolean
          description: >-
            True when the window wraps past local midnight (`startSecondsOfDay`
            > `endSecondsOfDay`), e.g. an 18:00→17:00 session.
    MarketType:
      type: string
      enum:
        - PERPETUAL
      description: Kind of market venue exposed for a symbol.
    MarketCategory:
      type: string
      enum:
        - CRYPTO
        - INDICES
        - EQUITIES
        - FOREX
        - COMMODITIES
      description: Underlying asset class of a market.
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