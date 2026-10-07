> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# WebSocket

> Real-time market data and order routing

Connect to `wss://api.testnet.arcus.xyz/v1/ws` (mainnet: `wss://api.arcus.xyz/v1/ws`). The connection multiplexes channel subscriptions and request/response (RPC) calls.

<Note>
  **Recommended client resources.** For a client consuming the real-time feeds, we recommend at least **2 CPU cores and 4 GB of RAM** so message parsing and local book maintenance keep up under load. High-throughput clients (many markets or deep order books) should provision more.
</Note>

## Message envelopes

**Subscribe / unsubscribe:**

```json theme={null}
{ "type": "subscribe",   "channel": "<channel_name>", "id": "<subscription_id>" }
{ "type": "unsubscribe", "channel": "<channel_name>", "id": "<subscription_id>" }
```

The server replies with `{ "type": "subscribed", ... }` whose `contents` **is the initial snapshot** for channels that deliver one — there is no separate bare acknowledgement frame. (A connection-health check should therefore count channels that delivered `contents`, not empty acks.) It then streams `{ "type": "channel_data", ... }` updates. Each update carries the `channel`, the subscription `id` (for per-market channels), and a `contents` payload. Timestamps are inside `contents` (e.g. `timestamp`, `epoch`, `markEpochNanos`); there is no top-level publish timestamp.

Optional subscribe fields:

| Field | Applies to | Meaning |
| - | - | - |
| `snapshot` | channels that stream deltas | `false` suppresses the initial snapshot so only live updates flow. Default `true`. Ignored (always `true`) on snapshot-only channels such as `markets`. |
| `nLevels` | `l2Orderbook`, `l2OrderbookUpdates` | Price levels per side, 1–100 (default 20). |
| `nRecentClosed` | `orders` | Closed orders included in the snapshot (default 100). |
| `nFills` | `userFills` | Fills included in the snapshot (default 500, max 500). |

**Request / response:** every `post` and `get` message must include a numeric `id` chosen by the client. The server echoes the same `id` in its response. IDs do not need to increase — uniqueness among in-flight calls on the connection is sufficient.

```json theme={null}
{
  "type": "get",
  "id": 2,
  "request": {
    "type": "l2orderbook",
    "payload": { "market": "BTC-USD" }
  }
}
```

See [Authentication](/api-reference/authentication) for the signing rules that apply to order methods.

## What authorizes a subscription

Nothing. **Subscribing is never authenticated** — not even on the account-scoped channels (`account`, `positions`, `orders`, `userFills`). A `subscribe` frame carries no signature, the socket is not bound to an API key, and there is no `authenticate` step to miss: any connection may subscribe to **any** address and will receive that address's balances, positions, open orders, and fills.

This is deliberate, not an oversight. These channels are the streaming form of the account-scoped REST reads (`GET /v1/fills`, `/v1/orders`, `/v1/positions`, `/v1/account`), which are themselves public and take `?address=` without an API key. Account state on Arcus is **readable by anyone who knows the address**; the API key authorizes *writes* only.

<Warning>
  Your positions and fills are observable in real time by anyone who knows your address. If a strategy depends on that activity not being attributable, separate it by address — the API offers no per-address read restriction.
</Warning>

The WebSocket `authenticate` method exists, but it is **not** a prerequisite for subscribing or for signed order methods (each order carries its own signature). See [Authentication](/api-reference/authentication).

## Placing orders

Order methods are asynchronous. The server returns `202` with `status: "ACK"` (or `CANCEL_ACKNOWLEDGED`); subscribe to the [`orders`](/api-reference/channels#orders) or [`userFills`](/api-reference/channels#userfills) channels to observe the lifecycle.

```json theme={null}
{
  "type": "post",
  "id": 1,
  "request": {
    "type": "placeOrder",
    "payload": {
      "address": "0x...",
      "accountIndex": 0,
      "marketId": 1,
      "orderSide": "BUY",
      "orderType": "LIMIT",
      "timeInForce": "GTT",
      "goodTilTime": "4102444800000000",
      "quantity": "0.01",
      "price": "50000"
    },
    "apiKey": "<64-hex-public-key>",
    "timestamp": "1712345678000000000",
    "signature": "<128-hex-signature>"
  }
}
```

Server response:

```json theme={null}
{
  "method": "placeOrder",
  "id": 1,
  "status": 202,
  "result": { "orderId": "...", "status": "ACK" }
}
```

<Warning>
  **No cancel-on-disconnect.** Dropping a WebSocket connection tears down your subscriptions but does **not** cancel your resting orders. An order rests until it fills, you explicitly cancel it, or its `goodTilTime` expires (an expired resting order is removed when matching reaches it and reported on the `orders` channel as `REJECTED` / `EXPIRED`), and `goodTilTime` must be at least \~1 month out (enforced on every order, including `IOC`/`FOK`). If you need orders pulled when your process dies, arm the [dead man's switch](#dead-mans-switch-schedulecancel) or run your own external kill-switch that cancels on disconnect.
</Warning>

## Dead man's switch (`scheduleCancel`)

`scheduleCancel` is a server-side dead man's switch: you arm a deadline, and if you don't refresh it before it elapses, the gateway automatically cancels your resting orders. Use it so a crashed or disconnected trading process doesn't leave stale quotes on the book.

It's a signed `post` method — same envelope and signing rules as other order methods (see [Authentication](/api-reference/authentication)) — and is also available over REST as `POST /v1/scheduleCancel`.

* **Arm or refresh** — send `time`, an absolute epoch-**microsecond** deadline between **5 seconds and 5 minutes** in the future. Keep the switch alive by sending a new, later `time` before the current one elapses (typically `now + 60s` on a short refresh interval); reusing a past `time` does not extend it.
* **Disarm** — omit `time` (or send `null`).

**Scope.** Omit `marketId` for an account-wide switch (cancels every open order across all markets when it fires), or set it to scope the switch to a single market. Per-market switches are independent of the account-wide switch and of each other on the same subaccount — each keeps its own deadline and cancels only its own market — so you can run one process per market, each arming its own switch. `marketId` selects which switch a given arm/refresh/disarm targets.

```json theme={null}
{
  "type": "post",
  "id": 5,
  "request": {
    "type": "scheduleCancel",
    "payload": { "address": "0x...", "accountIndex": 0, "time": 1734567890123000 },
    "apiKey": "<64-hex-public-key>",
    "timestamp": "1712345678000000000",
    "signature": "<128-hex-signature>"
  }
}
```

Add `"marketId": 1` to the payload to scope the switch to a single market.

**Limits.** Two quotas bound the switch, on top of the per-subaccount cancel pool that every arm/refresh draws from (see [Rate limits](/api-reference/rate-limits#dead-mans-switch-quotas)):

* **Auto-fires** are capped at **10 per UTC day per subaccount**, shared across all of that subaccount's switches.
* A **wallet** may hold at most **50 switches armed at once**, across all its subaccounts and markets. Arming a new switch beyond that returns `429`; refreshing an already-armed switch is always allowed.

When a switch fires, the resulting cancellations stream over the [`orders`](/api-reference/channels#orders) channel like any other cancel.

## Channels

See the [Channels](/api-reference/channels) reference for the full list and per-channel payload schemas.

## Sequence numbers

Streamed messages carry sequence numbers so you can order events, detect dropped messages, and resync after a reconnect. Three scopes exist:

* **Global** — one monotonic counter across the whole exchange, incremented for every event the matching engine processes. Use it to order events across markets and to tell whether you've missed anything.
* **Per-market** — a counter local to a single market.
* **Per-account** — a counter local to a single account.

Which field carries the sequence depends on the message:

| Scope | Field | Appears on |
| - | - | - |
| Global | `globalSequenceId` | `l2Orderbook`, `l2OrderbookUpdates`, `bbo` |
| Global | `sequenceNumber` | `trades` |
| Per-market (order book) | `lastSequenceId` | `l2Orderbook`, `l2OrderbookUpdates`, `bbo` |
| Per-market (market attributes) | `marketSequenceNum` | `exchangeAttributeUpdates` (`marketAttributes` entries) |
| Per-account | `sequenceNumber` | `AccountUpdate` (`AccountTransferEvent` exposes the same value as `accountSequenceNum`) |
| Per-account | `lastSequenceId` | `account` and `positions` snapshots |

The `lastSequenceId` on `account` and `positions` snapshots is the per-account sequence at snapshot time — the same counter as `AccountUpdate.sequenceNumber` and `Order.sequenceNumber`. Streaming `positions` deltas carry this same per-account counter: each `PositionUpdate` envelope stamps `lastSequenceId` and each position row carries `sequenceNumber`, so a delta can be ordered directly against account updates and used to discard updates already contained in a snapshot. For account state, treat each snapshot as the reconciliation point: the `account` channel re-snapshots every 5 seconds, and a re-subscribe fetches a fresh snapshot on any other account-scoped channel.

### Resyncing the order book

For `l2Orderbook`, the snapshot's `lastSequenceId` is the per-market sequence of the last update it reflects. Seed from the snapshot, then apply every `l2OrderbookUpdates` delta whose `lastSequenceId` is **greater** than the snapshot's. The snapshot is a periodic generation that lags slightly behind the live delta head, so the first delta may be a few sequences ahead of the snapshot's `lastSequenceId` — this boundary gap is expected and self-heals, so do **not** re-subscribe on it. Once you are applying deltas, sequences are contiguous: a gap that appears **mid-stream** means you missed a delta, and only then should you re-subscribe for a fresh snapshot. Use `globalSequenceId` to order order-book events against other markets.

### Order-book sequence contiguity

The order-book channels share the same per-market `lastSequenceId`, but only `l2OrderbookUpdates` (and the `l2Orderbook` snapshot it baselines against) is **contiguous** — the only stream where a gap is meaningful. Treat each field by its guarantee:

| Field / channel | Contiguous per subscription? | Handling |
| - | - | - |
| `lastSequenceId` on `l2Orderbook` / `l2OrderbookUpdates` | Contiguous once you are applying deltas; the first delta after a snapshot may be a few sequences ahead (expected boundary gap) | Seed from the snapshot and apply deltas whose `lastSequenceId` exceeds it; do **not** re-subscribe on the initial boundary gap. A gap that appears **mid-stream** means a missed delta — re-subscribe for a fresh snapshot then. |
| `lastSequenceId` on `bbo` | No — advances past suppressed updates | `bbo` emits a frame only when the top of book changes, so the sequence legitimately **jumps forward** over depth-only updates. A forward jump is normal — do **not** treat it as a dropped frame or re-subscribe. |
| `globalSequenceId` (any channel) | No — cross-market counter | Other markets consume values, so it skips within a single market's stream. It's an ordering key only; never gap-check it. |

`bbo` is a snapshot channel: every frame carries the full current top of book and self-replaces the previous one, so a dropped frame self-heals on the next — there is no in-band gap detection and none is needed. If you need a contiguous, gap-detectable, replayable order-book stream, use `l2Orderbook` / `l2OrderbookUpdates`.

The per-market `lastSequenceId` does not reset on a reconnect, but don't assume continuity across the reconnect gap. On every (re)subscribe, take the fresh snapshot's `lastSequenceId` as the new baseline and resume gap-checking from there.

## Errors

Responses include an HTTP-like `status` and either a `result` or an `error` object with `type`, `message`, and optional `field`, `errorType` / `errorSource` (machine-readable code and the layer that raised it; `errorType: Transmission` means the order never reached the matching engine and is safe to resend unchanged) and `retryAfterMs` (on `429`).


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.