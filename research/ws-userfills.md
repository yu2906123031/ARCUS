> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# User fills

> > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every channel and RPC request. Click **Connect**, then send the example subscribe message below to start receiving data.

Per-user fill stream. Snapshot on subscribe contains recent fills; streaming updates deliver each new fill as orders are matched. Snapshot is sent once on connect only.

`LIQUIDATION` and `ADL` fills also appear here carrying the `liquidation` marker (`method` + `liquidatedUser`) — on the liquidated account's leg for `LIQUIDATION` and the deleveraged counterparty's leg for `ADL` — so subscribers tracking realized closures can observe forced fills on either `account` or `userFills`. The marker is persisted, so it is present in snapshots as well as live updates.

Optionally scoped to one market: pass `market` on subscribe (ticker or numeric id) and the snapshot, the live frames, and the post-snapshot catch-up all carry only that market. The filter is part of subscription identity, so one connection can hold several market-scoped views of the same account; `unsubscribe` must repeat the same `market`.




## AsyncAPI

````yaml api-reference/asyncapi.yaml userFills
id: userFills
title: User fills
description: >
  > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every
  channel and RPC request. Click **Connect**, then send the example subscribe
  message below to start receiving data.


  Per-user fill stream. Snapshot on subscribe contains recent fills; streaming
  updates deliver each new fill as orders are matched. Snapshot is sent once on
  connect only.


  `LIQUIDATION` and `ADL` fills also appear here carrying the `liquidation`
  marker (`method` + `liquidatedUser`) — on the liquidated account's leg for
  `LIQUIDATION` and the deleveraged counterparty's leg for `ADL` — so
  subscribers tracking realized closures can observe forced fills on either
  `account` or `userFills`. The marker is persisted, so it is present in
  snapshots as well as live updates.


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
address: /v1/ws?channel=userFills
parameters: []
bindings: []
operations:
  - &ref_2
    id: subscribeUserFills
    title: Subscribe user fills
    type: receive
    messages:
      - &ref_4
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
    extensions: &ref_1
      - id: x-parser-unique-object-id
        value: userFills
  - &ref_3
    id: receiveUserFills
    title: Receive user fills
    type: send
    messages:
      - &ref_5
        id: snapshot
        contentType: application/json
        payload:
          - name: User Fills Snapshot
            description: Recent fills sent once on subscribe.
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
        title: User Fills Snapshot
        description: Recent fills sent once on subscribe.
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
      - &ref_6
        id: update
        contentType: application/json
        payload:
          - name: User Fill Update
            description: One fill per frame as orders are matched.
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
        jsonPayloadSchema: *ref_0
        title: User Fill Update
        description: One fill per frame as orders are matched.
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
            value: update
    bindings: []
    extensions: *ref_1
sendOperations:
  - *ref_2
receiveOperations:
  - *ref_3
sendMessages:
  - *ref_4
receiveMessages:
  - *ref_5
  - *ref_6
extensions:
  - id: x-parser-unique-object-id
    value: userFills
securitySchemes: []

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.