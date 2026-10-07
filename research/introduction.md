> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Introduction

> REST and WebSocket APIs for Arcus

Arcus exposes a REST API for account management, perpetuals trading, and historical data, plus a WebSocket API for real-time market data and order routing. These APIs cover Arcus **perpetuals**. Both are non-custodial: you sign with an Ed25519 API key registered against your Ethereum address, and the server never sees a private key.

<Note>
  **Fund your account before trading.** A newly-registered API key starts with **no balance** — `GET /v1/account` returns `404` until you make a deposit. Use the **Testnet Deposit** button in the testnet web app for a quick \~\$1,000, or deposit on-chain via the bridge. See [Your testnet account](/api-reference/authentication#your-testnet-account).
</Note>

## Base URLs

| Environment | REST | WebSocket |
| - | - | - |
| Mainnet | `https://api.arcus.xyz` | `wss://api.arcus.xyz/v1/ws` |
| Testnet | `https://api.testnet.arcus.xyz` | `wss://api.testnet.arcus.xyz/v1/ws` |

Both environments share identical paths, payloads, and signing — only the host differs, selectable from the server dropdown on each endpoint page. The code samples and API playground throughout these docs default to **mainnet**. New API features ship to testnet first, so the two environments can differ for a while: the [changelog](/changelog) tracks what is live in production, and the [testnet changelog](/changelog/testnet) tracks spec changes as they land on testnet ahead of it.

## Where to next

<CardGroup cols={2}>
  <Card title="Authentication" icon="key" href="/api-reference/authentication">
    Generate an Ed25519 key, register it, and sign requests.
  </Card>

  <Card title="WebSocket overview" icon="bolt" href="/api-reference/websocket">
    Connect, send subscribe / post / get envelopes, and place orders.
  </Card>

  <Card title="Channels" icon="list" href="/api-reference/channels">
    Per-channel reference: market data, order book, account, candles.
  </Card>

  <Card title="REST endpoints" icon="code" href="/api-reference/public/get-markets">
    Auto-generated from the OpenAPI spec — see the **Endpoints** group in the sidebar.
  </Card>

  <Card title="Rate limits" icon="gauge-high" href="/api-reference/rate-limits">
    Per-IP request weights, per-subaccount trading pools, and WebSocket caps.
  </Card>
</CardGroup>


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.