> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Bbo

> > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every channel and RPC request. Click **Connect**, then send the example subscribe message below to start receiving data.

Best bid and offer (top-of-book) for a single market. Lower bandwidth than `l2Orderbook` when only the inside quote is needed.




## AsyncAPI

````yaml api-reference/asyncapi.yaml bbo
id: bbo
title: Bbo
description: >
  > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every
  channel and RPC request. Click **Connect**, then send the example subscribe
  message below to start receiving data.


  Best bid and offer (top-of-book) for a single market. Lower bandwidth than
  `l2Orderbook` when only the inside quote is needed.
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
address: /v1/ws?channel=bbo
parameters: []
bindings: []
operations:
  - &ref_2
    id: subscribeBBO
    title: Subscribe b b o
    type: receive
    messages:
      - &ref_4
        id: subscribe
        contentType: application/json
        payload:
          - name: Subscribe to a market channel
            type: object
            properties:
              - name: type
                type: string
                description: subscribe
                required: true
              - name: channel
                type: string
                enumValues:
                  - trades
                  - oraclePrices
                  - bbo
                required: true
              - name: id
                type: string
                description: Market display name (e.g. "BTC-USD").
                required: true
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
              x-parser-schema-id: <anonymous-schema-43>
            channel:
              type: string
              enum:
                - trades
                - oraclePrices
                - bbo
              x-parser-schema-id: <anonymous-schema-44>
            id:
              type: string
              description: Market display name (e.g. "BTC-USD").
              example: BTC-USD
              x-parser-schema-id: <anonymous-schema-45>
          x-parser-schema-id: SubscribeByMarketPayload
        title: Subscribe to a market channel
        example: |-
          {
            "type": "subscribe",
            "channel": "trades",
            "id": "BTC-USD"
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: subscribe
    bindings: []
    extensions: &ref_1
      - id: x-parser-unique-object-id
        value: bbo
  - &ref_3
    id: receiveBBO
    title: Receive b b o
    type: send
    messages:
      - &ref_5
        id: update
        contentType: application/json
        payload:
          - allOf: &ref_0
              - type: object
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
                      Channel id (market, or the lowercase master Ethereum
                      address on account-scoped channels). Absent for global
                      channels. Always lowercase for addresses regardless of the
                      spelling used to subscribe, so compare it
                      case-insensitively.
                    x-parser-schema-id: <anonymous-schema-9>
                  accountIndex:
                    type: integer
                    description: >
                      Subaccount this frame belongs to. Present on EVERY frame
                      of an account-scoped channel — `subscribed`,
                      `unsubscribed`, `degraded`, and `channel_data` alike —
                      including when it is 0, so a frame names its subaccount
                      outright and no client needs a "missing means 0" rule.
                      Omitted on every other channel. This is what makes several
                      subaccounts of one address demuxable on a single
                      connection: `id` alone is the master address and is
                      identical across all of them.
                    minimum: 0
                    maximum: 9
                    example: 3
                    x-parser-schema-id: <anonymous-schema-10>
                  sigFigs:
                    type: integer
                    description: >
                      Echo of the l2Orderbook aggregation this frame belongs to.
                      Omitted on full-precision views and on every other
                      channel. Lets a client holding several aggregations of the
                      same market demux snapshots.
                    enum:
                      - 2
                      - 3
                      - 4
                      - 5
                    x-parser-schema-id: <anonymous-schema-11>
                  roundStep:
                    type: integer
                    description: >-
                      Echo of the l2Orderbook roundStep for this view. Omitted
                      unless sigFigs is 5 and roundStep was 2 or 5.
                    enum:
                      - 1
                      - 2
                      - 5
                    x-parser-schema-id: <anonymous-schema-12>
                  market:
                    type: string
                    example: BTC-USD
                    description: >
                      Display ticker of the market this frame belongs to, always
                      the resolved ticker even when the client subscribed by
                      numeric id. Present only on `positions` / `orders` /
                      `userFills`; omitted on every other channel. Its meaning
                      differs slightly by frame type. On `subscribed` /
                      `unsubscribed` it echoes the market filter the
                      subscription was created with, and is omitted for an
                      unfiltered subscription. On `channel_data` it is the
                      market of the frame itself, so an UNFILTERED subscriber
                      sees it too — those channels emit one frame per market, so
                      every frame has one. This is what lets a client demux,
                      exactly as `sigFigs` / `roundStep` do for l2Orderbook: a
                      connection holding several market-filtered views of one
                      account matches each frame to the right view by this
                      field. Clients that hold a single unfiltered view can
                      ignore it.
                    x-parser-schema-id: <anonymous-schema-13>
                  contents:
                    type: object
                    additionalProperties: true
                    description: Channel-specific payload. Schema varies by channel.
                    properties: {}
                    x-parser-schema-id: <anonymous-schema-14>
                x-parser-schema-id: ChannelDataEnvelope
              - type: object
                properties:
                  type:
                    const: channel_data
                    x-parser-schema-id: <anonymous-schema-122>
                  contents:
                    type: object
                    required:
                      - bestBid
                      - bestAsk
                      - timestamp
                      - lastSequenceId
                      - globalSequenceId
                    properties:
                      bestBid:
                        type: object
                        nullable: true
                        description: Best bid level, or null when the bid side is empty.
                        required:
                          - price
                          - size
                        properties:
                          price:
                            type: string
                            example: '94500.00'
                            x-parser-schema-id: <anonymous-schema-125>
                          size:
                            type: string
                            example: '1.5'
                            x-parser-schema-id: <anonymous-schema-126>
                        x-parser-schema-id: <anonymous-schema-124>
                      bestAsk:
                        type: object
                        nullable: true
                        description: Best ask level, or null when the ask side is empty.
                        required:
                          - price
                          - size
                        properties:
                          price:
                            type: string
                            example: '94501.00'
                            x-parser-schema-id: <anonymous-schema-128>
                          size:
                            type: string
                            example: '0.8'
                            x-parser-schema-id: <anonymous-schema-129>
                        x-parser-schema-id: <anonymous-schema-127>
                      timestamp:
                        type: integer
                        format: int64
                        description: Unix microseconds when the top-of-book was stamped.
                        example: 1780410803123456
                        x-parser-schema-id: <anonymous-schema-130>
                      lastSequenceId:
                        type: integer
                        description: Per-market orderbook sequence for this BBO frame.
                        example: 2001
                        x-parser-schema-id: <anonymous-schema-131>
                      globalSequenceId:
                        type: integer
                        description: Cross-market monotonic sequence.
                        example: 880422
                        x-parser-schema-id: <anonymous-schema-132>
                    x-parser-schema-id: <anonymous-schema-123>
                x-parser-schema-id: <anonymous-schema-121>
            x-parser-schema-id: BBOUpdatePayload
            name: Best Bid Offer (BBO) Update
        headers: []
        jsonPayloadSchema:
          allOf: *ref_0
          x-parser-schema-id: BBOUpdatePayload
        title: Best Bid Offer (BBO) Update
        example: |-
          {
            "type": "channel_data",
            "channel": "bbo",
            "id": "BTC-USD",
            "contents": {
              "bestBid": {
                "price": "94500.00",
                "size": "1.5"
              },
              "bestAsk": {
                "price": "94501.00",
                "size": "0.8"
              },
              "timestamp": 1780410803123456,
              "lastSequenceId": 2001,
              "globalSequenceId": 880422
            }
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
extensions:
  - id: x-parser-unique-object-id
    value: bbo
securitySchemes: []

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.