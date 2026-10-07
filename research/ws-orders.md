> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Orders

> > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every channel and RPC request. Click **Connect**, then send the example subscribe message below to start receiving data.

Open and recently closed order updates for an account. Snapshot on subscribe includes all open orders and up to 100 recently closed orders. Streaming updates use a unified shape for both status changes and fill events. Snapshot is sent once on connect only.

Subscribe to this channel alongside `userFills` to observe the full order lifecycle after placing an order via the `orderOperations` channel.

Optionally scoped to one market: pass `market` on subscribe (ticker or numeric id) and the snapshot, the live frames, and the post-snapshot catch-up all carry only that market. The filter is part of subscription identity, so one connection can hold several market-scoped views of the same account; `unsubscribe` must repeat the same `market`.




## AsyncAPI

````yaml api-reference/asyncapi.yaml orders
id: orders
title: Orders
description: >
  > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every
  channel and RPC request. Click **Connect**, then send the example subscribe
  message below to start receiving data.


  Open and recently closed order updates for an account. Snapshot on subscribe
  includes all open orders and up to 100 recently closed orders. Streaming
  updates use a unified shape for both status changes and fill events. Snapshot
  is sent once on connect only.


  Subscribe to this channel alongside `userFills` to observe the full order
  lifecycle after placing an order via the `orderOperations` channel.


  Optionally scoped to one market: pass `market` on subscribe (ticker or numeric
  id) and the snapshot, the live frames, and the post-snapshot catch-up all
  carry only that market. The filter is part of subscription identity, so one
  connection can hold several market-scoped views of the same account;
  `unsubscribe` must repeat the same `market`.
servers:
  - id: mainnet
    protocol: wss
    host: api.arcus.xyz
    bindings: []
    variables: []
  - id: testnet
    protocol: wss
    host: api.testnet.arcus.xyz
    bindings: []
    variables: []
address: /v1/ws?channel=orders
parameters: []
bindings: []
operations:
  - &ref_3
    id: subscribeOrders
    title: Subscribe orders
    type: receive
    messages:
      - &ref_5
        id: subscribe
        contentType: application/json
        payload:
          - name: Subscribe to an account channel
            type: object
            properties:
              - name: type
                type: string
                description: subscribe
                required: true
              - name: channel
                type: string
                enumValues:
                  - account
                  - positions
                  - userFills
                  - orders
                  - funding
                  - accountAttributeUpdates
                required: true
              - name: id
                type: string
                description: >
                  Master Ethereum address (0x-prefixed) or an encoded internal
                  account id. When an encoded id is used it already carries its
                  own subaccount index; supplying `accountIndex` as well is
                  allowed only if the two agree. Whichever spelling is sent,
                  every response frame echoes the lowercase master address.
                required: true
              - name: accountIndex
                type: integer
                description: >
                  Subaccount of the address in `id` to subscribe to. Honored on
                  the account-scoped channels — `account`, `positions`,
                  `orders`, `userFills`, `funding`, `accountTransferUpdates`,
                  `accountAttributeUpdates`. Omit for subaccount 0, which is
                  what every client written before this field existed gets. It
                  is part of subscription identity: each subaccount is a
                  separate subscription with its own snapshot and stream, so one
                  connection may hold several, and `unsubscribe` must repeat the
                  same `accountIndex` — omitting it drops only subaccount 0.
                  Supplying it on a channel that is not account-scoped (e.g.
                  `l2Orderbook`, `trades`, `markets`) is an error, NOT a
                  silently ignored field, for the same reason `market` is: a
                  client that believed its stream was scoped to one subaccount
                  would mis-handle the rest. A value outside 0-9 is likewise an
                  error rather than a clamp — an out-of-range subaccount can
                  never carry traffic, so accepting one returns a permanently
                  silent subscription.
                required: false
              - name: snapshot
                type: boolean
                description: >-
                  When false, suppress the initial state snapshot on subscribe
                  and stream only live updates (e.g. userFills, orders).
                  Defaults to true.
                required: false
              - name: nFills
                type: integer
                description: >
                  Optional userFills-only snapshot size (default 500, max 500).
                  Honored only on the userFills channel; ignored elsewhere. Live
                  channel_data frames are unaffected.
                required: false
              - name: market
                type: string
                description: >
                  Restrict this subscription to a single market. Honored only on
                  `positions`, `orders`, and `userFills` — the account-scoped
                  channels whose every frame is about exactly one market.
                  Supplying it on any other channel (e.g. `account`,
                  `accountTransferUpdates`) is an error, NOT a silently ignored
                  field: a client that believed its stream was scoped to one
                  market would mishandle every other market's data. Accepts the
                  display ticker ("BTC-USD", case-insensitive) or the numeric
                  market id ("1"), the same spellings as the REST `?market=`
                  query param. Omit for every market; an unresolvable value is
                  an error rather than an unfiltered stream. The filter applies
                  to the subscribe snapshot, the live `channel_data` frames, and
                  the post-snapshot catch-up alike, so the baseline and the
                  diffs always describe the same markets. It is part of
                  subscription identity: one connection may hold several
                  market-filtered views of the same channel + id, and
                  `unsubscribe` must repeat the market it subscribed with (an
                  `unsubscribe` with no `market` matches only the unfiltered
                  view). The `subscribed` / `unsubscribed` acks echo the
                  resolved ticker.
                required: false
        headers: []
        jsonPayloadSchema:
          type: object
          required:
            - type
            - channel
            - id
          properties:
            type:
              type: string
              const: subscribe
              x-parser-schema-id: <anonymous-schema-133>
            channel:
              type: string
              enum:
                - account
                - positions
                - userFills
                - orders
                - funding
                - accountAttributeUpdates
              x-parser-schema-id: <anonymous-schema-134>
            id:
              type: string
              description: >
                Master Ethereum address (0x-prefixed) or an encoded internal
                account id. When an encoded id is used it already carries its
                own subaccount index; supplying `accountIndex` as well is
                allowed only if the two agree. Whichever spelling is sent, every
                response frame echoes the lowercase master address.
              example: 0xAbCd...1234
              x-parser-schema-id: <anonymous-schema-135>
            accountIndex:
              type: integer
              description: >
                Subaccount of the address in `id` to subscribe to. Honored on
                the account-scoped channels — `account`, `positions`, `orders`,
                `userFills`, `funding`, `accountTransferUpdates`,
                `accountAttributeUpdates`. Omit for subaccount 0, which is what
                every client written before this field existed gets. It is part
                of subscription identity: each subaccount is a separate
                subscription with its own snapshot and stream, so one connection
                may hold several, and `unsubscribe` must repeat the same
                `accountIndex` — omitting it drops only subaccount 0. Supplying
                it on a channel that is not account-scoped (e.g. `l2Orderbook`,
                `trades`, `markets`) is an error, NOT a silently ignored field,
                for the same reason `market` is: a client that believed its
                stream was scoped to one subaccount would mis-handle the rest. A
                value outside 0-9 is likewise an error rather than a clamp — an
                out-of-range subaccount can never carry traffic, so accepting
                one returns a permanently silent subscription.
              minimum: 0
              maximum: 9
              default: 0
              example: 3
              x-parser-schema-id: <anonymous-schema-136>
            snapshot:
              type: boolean
              description: >-
                When false, suppress the initial state snapshot on subscribe and
                stream only live updates (e.g. userFills, orders). Defaults to
                true.
              default: true
              x-parser-schema-id: <anonymous-schema-137>
            nFills:
              type: integer
              description: >
                Optional userFills-only snapshot size (default 500, max 500).
                Honored only on the userFills channel; ignored elsewhere. Live
                channel_data frames are unaffected.
              default: 500
              maximum: 500
              minimum: 1
              x-parser-schema-id: <anonymous-schema-138>
            market:
              type: string
              example: BTC-USD
              description: >
                Restrict this subscription to a single market. Honored only on
                `positions`, `orders`, and `userFills` — the account-scoped
                channels whose every frame is about exactly one market.
                Supplying it on any other channel (e.g. `account`,
                `accountTransferUpdates`) is an error, NOT a silently ignored
                field: a client that believed its stream was scoped to one
                market would mishandle every other market's data. Accepts the
                display ticker ("BTC-USD", case-insensitive) or the numeric
                market id ("1"), the same spellings as the REST `?market=` query
                param. Omit for every market; an unresolvable value is an error
                rather than an unfiltered stream. The filter applies to the
                subscribe snapshot, the live `channel_data` frames, and the
                post-snapshot catch-up alike, so the baseline and the diffs
                always describe the same markets. It is part of subscription
                identity: one connection may hold several market-filtered views
                of the same channel + id, and `unsubscribe` must repeat the
                market it subscribed with (an `unsubscribe` with no `market`
                matches only the unfiltered view). The `subscribed` /
                `unsubscribed` acks echo the resolved ticker.
              x-parser-schema-id: <anonymous-schema-139>
          x-parser-schema-id: SubscribeByAddressPayload
        title: Subscribe to an account channel
        example: |-
          {
            "type": "subscribe",
            "channel": "account",
            "id": "0xAbCd...1234"
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: subscribe
    bindings: []
    extensions: &ref_2
      - id: x-parser-unique-object-id
        value: orders
  - &ref_4
    id: receiveOrders
    title: Receive orders
    type: send
    messages:
      - &ref_6
        id: snapshot
        contentType: application/json
        payload:
          - name: Orders Snapshot
            description: >
              Open orders plus recently closed orders with `isSnapshot: true`
              and `lastSequenceId` (per-account sequence at snapshot time). Sent
              once on subscribe.
            type: object
            properties:
              - name: type
                type: string
                description: >-
                  `subscribed` for the initial snapshot; `channel_data` for
                  updates.
                enumValues:
                  - subscribed
                  - channel_data
                required: true
              - name: channel
                type: string
                description: Channel name.
                required: true
              - name: id
                type: string
                description: >
                  Channel id (market, or the lowercase master Ethereum address
                  on account-scoped channels). Absent for global channels.
                  Always lowercase for addresses regardless of the spelling used
                  to subscribe, so compare it case-insensitively.
                required: false
              - name: accountIndex
                type: integer
                description: >
                  Subaccount this frame belongs to. Present on EVERY frame of an
                  account-scoped channel — `subscribed`, `unsubscribed`,
                  `degraded`, and `channel_data` alike — including when it is 0,
                  so a frame names its subaccount outright and no client needs a
                  "missing means 0" rule. Omitted on every other channel. This
                  is what makes several subaccounts of one address demuxable on
                  a single connection: `id` alone is the master address and is
                  identical across all of them.
                required: false
              - name: sigFigs
                type: integer
                description: >
                  Echo of the l2Orderbook aggregation this frame belongs to.
                  Omitted on full-precision views and on every other channel.
                  Lets a client holding several aggregations of the same market
                  demux snapshots.
                enumValues:
                  - 2
                  - 3
                  - 4
                  - 5
                required: false
              - name: roundStep
                type: integer
                description: >-
                  Echo of the l2Orderbook roundStep for this view. Omitted
                  unless sigFigs is 5 and roundStep was 2 or 5.
                enumValues:
                  - 1
                  - 2
                  - 5
                required: false
              - name: market
                type: string
                description: >
                  Display ticker of the market this frame belongs to, always the
                  resolved ticker even when the client subscribed by numeric id.
                  Present only on `positions` / `orders` / `userFills`; omitted
                  on every other channel. Its meaning differs slightly by frame
                  type. On `subscribed` / `unsubscribed` it echoes the market
                  filter the subscription was created with, and is omitted for
                  an unfiltered subscription. On `channel_data` it is the market
                  of the frame itself, so an UNFILTERED subscriber sees it too —
                  those channels emit one frame per market, so every frame has
                  one. This is what lets a client demux, exactly as `sigFigs` /
                  `roundStep` do for l2Orderbook: a connection holding several
                  market-filtered views of one account matches each frame to the
                  right view by this field. Clients that hold a single
                  unfiltered view can ignore it.
                required: false
              - name: contents
                type: object
                description: Channel-specific payload. Schema varies by channel.
                required: false
                properties: []
        headers: []
        jsonPayloadSchema: &ref_0
          type: object
          description: Common envelope for all server-pushed channel messages.
          required:
            - type
            - channel
          properties:
            type:
              type: string
              enum:
                - subscribed
                - channel_data
              description: >-
                `subscribed` for the initial snapshot; `channel_data` for
                updates.
              x-parser-schema-id: <anonymous-schema-7>
            channel:
              type: string
              description: Channel name.
              x-parser-schema-id: <anonymous-schema-8>
            id:
              type: string
              description: >
                Channel id (market, or the lowercase master Ethereum address on
                account-scoped channels). Absent for global channels. Always
                lowercase for addresses regardless of the spelling used to
                subscribe, so compare it case-insensitively.
              x-parser-schema-id: <anonymous-schema-9>
            accountIndex:
              type: integer
              description: >
                Subaccount this frame belongs to. Present on EVERY frame of an
                account-scoped channel — `subscribed`, `unsubscribed`,
                `degraded`, and `channel_data` alike — including when it is 0,
                so a frame names its subaccount outright and no client needs a
                "missing means 0" rule. Omitted on every other channel. This is
                what makes several subaccounts of one address demuxable on a
                single connection: `id` alone is the master address and is
                identical across all of them.
              minimum: 0
              maximum: 9
              example: 3
              x-parser-schema-id: <anonymous-schema-10>
            sigFigs:
              type: integer
              description: >
                Echo of the l2Orderbook aggregation this frame belongs to.
                Omitted on full-precision views and on every other channel. Lets
                a client holding several aggregations of the same market demux
                snapshots.
              enum:
                - 2
                - 3
                - 4
                - 5
              x-parser-schema-id: <anonymous-schema-11>
            roundStep:
              type: integer
              description: >-
                Echo of the l2Orderbook roundStep for this view. Omitted unless
                sigFigs is 5 and roundStep was 2 or 5.
              enum:
                - 1
                - 2
                - 5
              x-parser-schema-id: <anonymous-schema-12>
            market:
              type: string
              example: BTC-USD
              description: >
                Display ticker of the market this frame belongs to, always the
                resolved ticker even when the client subscribed by numeric id.
                Present only on `positions` / `orders` / `userFills`; omitted on
                every other channel. Its meaning differs slightly by frame type.
                On `subscribed` / `unsubscribed` it echoes the market filter the
                subscription was created with, and is omitted for an unfiltered
                subscription. On `channel_data` it is the market of the frame
                itself, so an UNFILTERED subscriber sees it too — those channels
                emit one frame per market, so every frame has one. This is what
                lets a client demux, exactly as `sigFigs` / `roundStep` do for
                l2Orderbook: a connection holding several market-filtered views
                of one account matches each frame to the right view by this
                field. Clients that hold a single unfiltered view can ignore it.
              x-parser-schema-id: <anonymous-schema-13>
            contents:
              type: object
              additionalProperties: true
              description: Channel-specific payload. Schema varies by channel.
              properties: {}
              x-parser-schema-id: <anonymous-schema-14>
          x-parser-schema-id: ChannelDataEnvelope
        title: Orders Snapshot
        description: >
          Open orders plus recently closed orders with `isSnapshot: true` and
          `lastSequenceId` (per-account sequence at snapshot time). Sent once on
          subscribe.
        example: |-
          {
            "type": "<string>",
            "channel": "<string>",
            "id": "<string>",
            "accountIndex": 123,
            "sigFigs": 123,
            "roundStep": 123,
            "market": "<string>",
            "contents": {}
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: snapshot
      - &ref_7
        id: update
        contentType: application/json
        payload:
          - allOf: &ref_1
              - *ref_0
              - type: object
                properties:
                  contents:
                    type: object
                    properties:
                      orderId:
                        type: string
                        x-parser-schema-id: <anonymous-schema-142>
                      clientId:
                        type: string
                        x-parser-schema-id: <anonymous-schema-143>
                      marketId:
                        type: integer
                        minimum: 0
                        maximum: 65535
                        description: >-
                          Perpetual market identifier (uint16). Map to display
                          name via `GET /markets`.
                        x-parser-schema-id: <anonymous-schema-144>
                      marketDisplayName:
                        type: string
                        example: BTC-USD
                        x-parser-schema-id: <anonymous-schema-145>
                      side:
                        type: string
                        enum:
                          - BUY
                          - SELL
                        x-parser-schema-id: <anonymous-schema-146>
                      status:
                        type: string
                        enum:
                          - OPEN
                          - FILLED
                          - CANCELED
                          - MARGIN_CANCELED
                          - REJECTED
                          - TPSL_PLACED
                          - TPSL_CANCELED
                          - TPSL_TRIGGERED
                        x-parser-schema-id: <anonymous-schema-147>
                      price:
                        type: string
                        description: >-
                          Limit price. All TPSL lifecycle events (TPSL_PLACED,
                          TPSL_TRIGGERED, TPSL_CANCELED, trigger-time REJECTED)
                          carry the leg's limit price here; market-type TPSLs
                          placed without a price report "0" — use triggerPrice
                          for the trigger level.
                        example: '94000.00'
                        x-parser-schema-id: <anonymous-schema-148>
                      originalSize:
                        type: string
                        example: '1.0'
                        x-parser-schema-id: <anonymous-schema-149>
                      remainingSize:
                        type: string
                        description: '"0.0" means fully filled.'
                        example: '0.5'
                        x-parser-schema-id: <anonymous-schema-150>
                      avgFillPrice:
                        type: string
                        description: >
                          Cumulative average fill price across all fills of this
                          order. Present whenever the order has any filled
                          quantity; absent when nothing has filled. Matches the
                          REST / snapshot `avgFillPrice`.
                        example: '94000.00'
                        x-parser-schema-id: <anonymous-schema-151>
                      triggerPrice:
                        type: string
                        description: >-
                          Set for TP/SL orders only — on TP/SL events and on a
                          REJECTED event for a TP/SL order rejected at placement
                          or at trigger time (e.g. a triggered reduce-only TP/SL
                          whose position flipped, or a triggered FOK that cannot
                          fully fill). A TP/SL that converted to a regular order
                          and was rejected after conversion does not carry TP/SL
                          fields.
                        x-parser-schema-id: <anonymous-schema-152>
                      tpslType:
                        type: string
                        enum:
                          - TAKE_PROFIT
                          - STOP_LOSS
                        description: Set for TP/SL orders only.
                        x-parser-schema-id: <anonymous-schema-153>
                      rejectionReason:
                        type: string
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
                          - OPEN_ORDER_CAP_EXCEEDED
                          - POSITION_TPSL_ALREADY_EXISTS
                          - ENTRY_TPSL_CANNOT_BE_POSITION_TPSL
                          - ORDER_WILL_TAKE_LIQUIDITY_DURING_MARKET_HALT
                          - ORDER_NOT_FOUND
                          - ORDER_NOT_FOUND_FOR_MODIFY
                          - MODIFY_CHANGED_IMMUTABLE_FIELD
                          - MODIFY_ZERO_SIZE
                          - EXPIRED
                          - PRICE_WILL_EXCEED_MAXIMUM_OUTSIDE_RTH_TRADING_BOUND
                          - MODIFY_WOULD_CROSS_OUTSIDE_RTH_TRADING_BOUNDARY
                          - FILL_WILL_EXCEED_TRADING_BOUND
                          - OPEN_INTEREST_CAP_EXCEEDED
                          - POSITION_SIZE_CAP_EXCEEDED
                          - MODIFY_TPSL_NOT_SUPPORTED
                          - MODIFY_SUPERSEDED_BY_CANCEL
                          - MODIFY_SIZE_ALREADY_FILLED
                        description: >
                          Machine-readable reason the matching engine rejected
                          an order. Present (non-empty) only when
                          `status`/`state` is `REJECTED`; this `orders` channel
                          is the source of truth for rejections.


                          | Value | Meaning (and side effect) |
                          |-------|---------------------------| |
                          `POST_ONLY_WOULD_CROSS` | A post-only order would have
                          crossed the book and taken liquidity; nothing rests. |
                          | `SELF_TRADE` | Order would have matched the
                          account's own resting order; blocked by self-trade
                          prevention. | | `UNDERCOLLATERALIZED` | Insufficient
                          free collateral / margin; order rejected, balances
                          unchanged (any earlier fills stand). | |
                          `COULD_NOT_FILL` | Legacy generic "could not fill";
                          prefer `IOC_CANCELED` / `FOK_FAILED`. | |
                          `IOC_CANCELED` | An immediate-or-cancel order produced
                          zero fills and was canceled. | | `FOK_FAILED` | A
                          fill-or-kill order could not be filled in full; no
                          fills committed. | | `REDUCE_ONLY_WOULD_INCREASE` | A
                          reduce-only order would open or grow the position
                          instead of reducing it. | | `TOO_MANY_CLIENT_IDS` |
                          Account is at its live `clientId` maximum; free a slot
                          before placing with a new `clientId`. | |
                          `DUPLICATE_CLIENT_ID` | The `clientId` already maps to
                          a live order; reusable after the prior order
                          terminates. | | `OPEN_ORDER_CAP_EXCEEDED` | Account is
                          at its simultaneous open-order maximum; cancel one or
                          let one terminate. No state change. | |
                          `POSITION_TPSL_ALREADY_EXISTS` | A position-level TPSL
                          of the same trigger class (TP or SL) already exists
                          for this account+market. | |
                          `ENTRY_TPSL_CANNOT_BE_POSITION_TPSL` | An entry-linked
                          TPSL cannot also be a position-level TPSL. | |
                          `ORDER_WILL_TAKE_LIQUIDITY_DURING_MARKET_HALT` |
                          **Deprecated** — no longer emitted; replaced by
                          `FILL_WILL_EXCEED_TRADING_BOUND`. Seen only on
                          historical orders. | | `ORDER_NOT_FOUND` | A cancel
                          matched no live order (already filled/canceled, never
                          existed, or unrecognised `clientId`). No state change.
                          | | `ORDER_NOT_FOUND_FOR_MODIFY` | A modify targeted
                          an order not on the regular book; TPSL/untriggered
                          orders cannot be modified. | |
                          `MODIFY_CHANGED_IMMUTABLE_FIELD` | A modify tried to
                          change an immutable field (`side`, `timeInForce`,
                          `reduceOnly`, `orderType`); only price and size may
                          change. | | `MODIFY_ZERO_SIZE` | A modify specified a
                          new size of zero or less; use cancel instead. | |
                          `EXPIRED` | A resting good-til-time order was removed
                          because its `goodTilTime` had elapsed; never filled,
                          reported `REJECTED`. No state change. | |
                          `PRICE_WILL_EXCEED_MAXIMUM_OUTSIDE_RTH_TRADING_BOUND`
                          | **Deprecated** — no longer emitted; replaced by
                          `FILL_WILL_EXCEED_TRADING_BOUND`. Seen only on
                          historical orders. | |
                          `MODIFY_WOULD_CROSS_OUTSIDE_RTH_TRADING_BOUNDARY` | A
                          non-crossing modify reprice violates the off-hours
                          band; the **original order stays resting** unchanged.
                          | | `FILL_WILL_EXCEED_TRADING_BOUND` | Outside regular
                          trading hours, the fill would breach the off-hours
                          bound; per-order reject (crossing modify
                          cancel-replace removes the original). In-band fills
                          stand. | | `OPEN_INTEREST_CAP_EXCEEDED` | Market is at
                          its open-interest cap and the next fill would raise
                          OI; matching halts, the unfilled remainder is
                          rejected, earlier fills stand. | |
                          `POSITION_SIZE_CAP_EXCEEDED` | Order/modify/fill would
                          exceed the per-market position notional cap (distinct
                          from `UNDERCOLLATERALIZED`). | |
                          `MODIFY_TPSL_NOT_SUPPORTED` | A modify targeted an
                          untriggered TPSL; a live TPSL survives unchanged. | |
                          `MODIFY_SUPERSEDED_BY_CANCEL` | A cancel for the same
                          order is pending and dominates; the modify is
                          discarded and the order ends canceled. | |
                          `MODIFY_SIZE_ALREADY_FILLED` | New total size is at or
                          below the filled quantity; the original order is
                          canceled (a `CANCELED` update precedes this
                          rejection). |
                        x-parser-schema-id: <anonymous-schema-154>
                      cancelReason:
                        type: string
                        enum:
                          - MODIFY_CANCELED
                        description: >
                          Qualifies a CANCELED update when the matching engine
                          stamped a reason. Currently the only value is
                          MODIFY_CANCELED: this cancel is the first leg of a
                          modify — the SAME orderId is re-placed by the
                          account's very next update (PLACED / FILLED /
                          REJECTED), so the order is not terminally gone unless
                          that follow-up says so. Absent on all other cancels,
                          which are genuinely terminal.
                        x-parser-schema-id: <anonymous-schema-155>
                      state:
                        type: string
                        enum:
                          - OPEN
                          - PARTIALLY_FILLED
                          - FILLED
                          - CANCELED
                          - REJECTED
                        description: >
                          Order lifecycle state after this event.
                          `PARTIALLY_FILLED` is terminal for IOC orders. Any
                          explicit cancel produces `CANCELED` even when prior
                          fills had already partially filled the order.
                        x-parser-schema-id: <anonymous-schema-156>
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
                          How this fill changed the account's position. Only set
                          on fill events; absent on placements/cancels/rejects.
                          `FLIP_*` covers a position that closed and reopened on
                          the opposite side within one user order.
                        x-parser-schema-id: <anonymous-schema-157>
                      createdAt:
                        type: integer
                        format: int64
                        description: >-
                          Order placement time (epoch microseconds); stable
                          across updates.
                        x-parser-schema-id: <anonymous-schema-158>
                      updatedAt:
                        type: integer
                        format: int64
                        description: Time of this update event (epoch microseconds).
                        x-parser-schema-id: <anonymous-schema-159>
                      sequenceNumber:
                        type: integer
                        format: uint64
                        description: >-
                          Per-account sequence of this order event (same counter
                          as AccountUpdate.sequenceNumber).
                        x-parser-schema-id: <anonymous-schema-160>
                    x-parser-schema-id: <anonymous-schema-141>
                x-parser-schema-id: <anonymous-schema-140>
            x-parser-schema-id: OrderUpdatePayload
            name: Order Update
            description: >
              One order lifecycle event. Carries `sequenceNumber` (per-account,
              same counter as AccountUpdate).
        headers: []
        jsonPayloadSchema:
          allOf: *ref_1
          x-parser-schema-id: OrderUpdatePayload
        title: Order Update
        description: >
          One order lifecycle event. Carries `sequenceNumber` (per-account, same
          counter as AccountUpdate).
        example: |-
          {
            "type": "channel_data",
            "channel": "orders",
            "id": "0xAbCd...1234",
            "contents": {
              "orderId": "ord-abc123",
              "clientId": "42",
              "marketId": 1,
              "marketDisplayName": "BTC-USD",
              "side": "BUY",
              "status": "FILLED",
              "price": "94000.00",
              "originalSize": "1.0",
              "remainingSize": "0.0",
              "avgFillPrice": "94000.00",
              "state": "FILLED",
              "positionEffect": "OPEN_LONG",
              "sequenceNumber": 5001
            }
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: update
    bindings: []
    extensions: *ref_2
sendOperations:
  - *ref_3
receiveOperations:
  - *ref_4
sendMessages:
  - *ref_5
receiveMessages:
  - *ref_6
  - *ref_7
extensions:
  - id: x-parser-unique-object-id
    value: orders
securitySchemes: []

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.