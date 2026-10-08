# Arcus BTC-USD 独立做市机器人

Python 3.12，命令行默认纸面模式，使用真实 Arcus 行情。一键入口默认 BTC-USD 实盘：5 倍杠杆、100 美元资金计算上限、仅软件保护，持续运行直到手动停止。Mid 或 Grid 同一时间只运行一种。只借鉴 [treading-bot README 的职责拆分流程](https://github.com/dhruvamity/treading-bot)，代码独立编写，未复制该仓库源码。没有 autopilot、Telegram、多账户交易或对倒流程。

**实验软件。回测和纸面结果不等于实盘收益。** 做市会承担逆向选择、资金费、滑点及持仓亏损；更多 maker 成交不保证盈利或低磨损。协议依据 [Arcus 官方认证文档](https://docs.arcus.xyz/api-reference/authentication)，详细核对结果见 [MM_PROTOCOL.md](docs/MM_PROTOCOL.md)。

## 文件结构与流程

~~~text
arcus_mm/
  __main__.py      CLI、单进程锁、退出清理
  config.py        配置验证
  models.py        精度、订单、持仓、损益、JSONL 日志
  signing.py       官方 Ed25519 两种签名方案
  api.py           REST、BBO/trades/orders/userFills WS、账户核对
  strategy.py      Mid/Grid 目标报价与潜在持仓预算
  paper.py         保守模拟成交
  engine.py        订单管理、风控、止损 IOC、断线保护
mm_config.json    无密钥的默认纸面配置
tests/test_mm.py  离线测试
docs/MM_PROTOCOL.md
research/*.md    核对时保存的官方文档
mm_logs/         运行生成的日志，已忽略
mm_runtime.lock  单进程锁，已忽略
~~~

流程：配置 → REST 元数据/费用/时钟 → WS 订阅 → 账户核对与风控 → 目标报价 → 撤单确认 → ALO 下单 → 成交/订单回报 → 持仓与损益。实盘先核对 API key、确认杠杆设置、启用服务端 dead man's switch。

新入口是 `python -m arcus_mm`。旧现货工具及动量纸面工具保留；旧 README 在 [LEGACY_README.md](docs/LEGACY_README.md)，旧永续说明在 [PERPS.md](PERPS.md)。

## 安装与纸面运行

### Windows 一键启动

双击 [一键运行.bat](一键运行.bat)。脚本自动定位 Python 3.12、检查依赖，使用主网持续运行 BTC-USD 实盘交易，直到按 Ctrl-C 手动停止，结束时请求 reduce-only 平仓；配置读取 mm_live_btc_100.json，资金计算上限 100 美元、杠杆 5 倍，单笔名义金额为权益 10%、最大持仓名义金额为权益 25%、浮亏止损为启动权益 1%、断线容忍 5 秒、连续下单失败 2 次停止，日志写入 mm_logs/live-BTC-USD，密钥从环境变量或 .env 读取。Ctrl-C 请求正常停止，不要直接关闭窗口。已有进程时单进程锁会拒绝重复启动，不会自动删除锁或重启风险停止的会话。

也可在终端运行 `".\一键运行.bat" --check`（cmd）进行实盘账户只读检查。双击脚本默认实盘，无需额外传入 --live。

时钟同步每轮采样 3 次，丢弃超时或 RTT 超过 1000 ms 的响应，采用最快有效样本。5 秒偏差保护保持不变；没有有效样本时先撤单暂停报价、保留最后一次有效校时值，可靠采样恢复后才继续。持续不可校时仍由断线时限停止，真实偏差超限或本地时钟跳变立即停止。

在项目目录内使用 Python 3.12：

~~~powershell
C:\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m arcus_mm doctor
.\.venv\Scripts\python.exe -m arcus_mm run --seconds 3600 --flatten-on-exit
~~~

不需要密钥。`--seconds 0`（默认）持续运行，时间限制包括启动检查所用时间。Grid：

~~~powershell
.\.venv\Scripts\python.exe -m arcus_mm run --strategy grid --seconds 3600 --flatten-on-exit
~~~

Ctrl-C 请求正常停止。默认停止撤单、保留持仓；`--flatten-on-exit` 在正常停止或时间到达时额外请求 reduce-only IOC 平仓。止损始终请求平仓。断线、时钟异常、连续失败等风险停止撤单并停止开新单，可能保留持仓。风险停止不会自动重启。实盘重启要求先恢复平仓且无挂单；每次纸面启动使用新账本。

## 配置

修改 [mm_config.json](mm_config.json)，支持 `--config 路径`。小数推荐写为字符串。

| 字段 | 默认 | 含义 |
|---|---:|---|
| api_url / ws_url | 官方主网 | REST / WS 地址，必须启用 TLS |
| market | BTC-USD | 当前仅支持此市场 |
| strategy | mid | mid 或 grid，CLI 可覆盖 |
| spread_bps | 2 | 每侧到报价中心的距离，两侧总宽约 4 bps |
| bias_bps | 0 | 中心上移为正，下移为负；允许 -3 至 +3 |
| order_equity_fraction | 0.1 | 每笔名义价值约为当前权益 10%，不乘杠杆 |
| max_position_equity_fraction | 0.5 | 净持仓名义价值上限为当前权益 50% |
| stop_loss_equity_fraction | 0.02 | 浮亏达到本次启动权益 2% 时停止并平仓 |
| leverage_cap | 3 | 实盘设置并确认该杠杆，仅允许 1–5 |
| paper | true | false 也必须显式 --live 才能运行 |
| paper_equity | 100 | 纸面初始权益，实盘从账户读取 |
| disconnect_seconds | 10 | 断线、行情过期或订阅不完整超限停止 |
| max_clock_skew_ms | 5000 | 时钟偏差加 RTT/2 的上限 |
| clock_samples | 3 | 每轮时钟采样次数，允许 2–5 |
| clock_max_rtt_ms | 1000 | 样本最大 RTT，同时作为每次请求超时，允许 100–2000 ms |
| max_order_failures | 3 | 连续失败停止，不明提交结果立即停止 |
| quote_interval_seconds | 1 | 报价决策间隔 |
| account_refresh_seconds | 2 | 实盘账户和成交 REST 核对间隔 |
| confirmation_seconds | 8 | 订单回报等待上限 |
| paper_latency_ms | 150 | 纸面订单开始允许成交的延迟 |
| paper_exit_slippage_bps | 5 | 纸面退出额外不利滑点 |
| exit_slippage_bps | 20 | 平仓 IOC 的限价滑点边界 |
| log_dir | mm_logs | JSONL 日志目录 |

Mid 在报价中心两侧各一笔；Grid 上下各两档，距离为 spread_bps 和 2 × spread_bps。按 tick/tickTiers 取整价格，按 step 向下取整数量。不足最小数量或最小名义价值时跳过，不自动放大订单。最低权益约为 `max(minOrderNotional, minOrderSize × 报价) / order_equity_fraction`，实际需要价格与费用余量；doctor 输出当前限制。

目标价偏离**超过一个基础 tick** 才因价格变化撤单重挂，等于一个 tick 不重挂；不因小幅数量变化刷接口。风控、reduce-only 属性改变、失去合法目标价、断线和停止会触发必要撤单。撤单确认前不重挂。做市全部使用 ALO，本地禁止报价与已有反向订单交叉，服务端 SELF_TRADE 拒绝会停止会话。

BUY 与 SELL 分别计算最坏成交后的潜在持仓，计入未成交和待确认数量，不把两边抵销。净持仓达到或超过上限时只保留对应方向的 reduce-only 报价。

## 注册 Arcus Ed25519 API key

按官方 [Authentication](https://docs.arcus.xyz/api-reference/authentication) 和 [REST trading guide](https://docs.arcus.xyz/guides/rest-trading)：

1. 打开官方主网 [API Keys](https://app.arcus.xyz/api-keys) 或 [测试网 API Keys](https://testnet.arcus.xyz/api-keys)，连接所属 Ethereum 钱包。
2. 选择专用子账户，生成并注册 Ed25519 API key，确认公钥、子账户和有效期，妥善保存 API 私钥。机器人接受 32 字节 seed（64 个 hex 字符），不能使用 Ethereum 钱包私钥。
3. 手动注册须生成 Ed25519 密钥对，并由所属 Ethereum 钱包以 **EIP-712 typed data** 签署 `POST /v1/createApiKey`。准确的域、类型和子账户字段以 [Create API key 官方文档](https://docs.arcus.xyz/api-reference/onboarding/create-api-key) 为准。机器人不代管主钱包私钥、不自动注册或存款。
4. 在网页给专用子账户存入抵押品。注册 key 不会充值，空账户可能返回 404；测试网可使用官方 Testnet Deposit。

密钥只从进程环境变量读取。通过系统环境变量界面或安全的会话注入方式设置，避免把私钥写入脚本、命令历史、配置或版本控制：

| 环境变量 | 内容 |
|---|---|
| ARCUS_ADDRESS | 注册 key 的所属 Ethereum 地址，0x + 40 hex |
| ARCUS_API_PRIVATE_KEY | Ed25519 API key 的 32 字节 seed，64 hex，可带 0x |
| ARCUS_ACCOUNT_INDEX | 子账户 0–9，默认 0 |

程序从 seed 派生公钥，在 /v1/apiKeys 核对 ACTIVE、子账户和有效期；日志不输出私钥、签名或认证头。

## 小资金实盘

先纸面运行并检查故障和磨损日志。使用**专用且平仓、无挂单的单个子账户**；运行期间不要在该子账户手动下单、转账或运行其他机器人。首次小资金测试可将 leverage_cap 改为 1；按真实最小数量配置足够但有限的权益和单笔比例，不足时跳过报价。

可先把两个 API 地址改成测试网进行接口验证，key 与账户环境必须一致。主网运行：

~~~powershell
.\.venv\Scripts\python.exe -m arcus_mm doctor --live
.\.venv\Scripts\python.exe -m arcus_mm run --live --seconds 300 --flatten-on-exit
~~~

doctor --live 只读检查，不发送变更或订单。run --live 才设置杠杆、启用保护并下单。paper=true 可被显式 --live 覆盖，移除 --live 就是纸面。

服务端 dead man's switch 按 BTC 市场设置，约每断线阈值的三分之一刷新，提前 5 秒至 5 分钟。停止后保留已武装的开关作为后备；它只撤单，不平仓。API 完全不可达时不能保证止损平仓成功：非零退出并记录残仓或确认失败，需在官方网页处理。IOC 都是 reduce-only，部分平仓后继续，最多 10 次，平仓确认上限 60 秒。

不明提交结果不会自动重发，HTTP ACK 不算成交。WS 重连重新订阅，账户、成交及 sequence 核对一致前不开新单，超时保持停止。订单/成交去重内存达到边界也会停止，不自动切换策略或重新部署。

## 日志与磨损

mm_logs/paper-*.jsonl 和 live-*.jsonl 记录每次下单、撤单、成交、持仓、均价、已实现损益、费用、maker 成交名义价值和估算磨损。

- realized_pnl：本会话均价成本法的价格已实现损益，手续费另列为 fees。
- estimated_exit_pnl：已实现损益减费用，加按当前对手价估算的持仓损益，再扣估算退出手续费。
- estimated_wear_usd = max(0, -estimated_exit_pnl)；wear_per_dollar 为磨损除以成交名义价值，零成交为 null。
- 磨损不是完整收益报表：资金费、未来退出深度及市场冲击未计入。实盘风控同时读取真实账户权益，实际成交费用从回报获取。纸面及退出估算使用费用等级 0，可能比实际账户费率更高。

纸面使用真实 BBO 与公开成交，不读取密钥、不发变更。模拟 maker 须严格穿价（只触及不算）、延迟届满、成交量够用，且买价不优于当时卖一、卖价不优于当时买一，同时不突破限价。一笔公开成交量不会被多档复用，tradeId 去重，旧成交不配更新的 BBO。该对手价约束会少计正常 maker 成交，不能精确复现队列和真实撮合。纸面 IOC 最多消耗对手买卖一可见数量，加入不利滑点，残仓会报告。

## 验证

~~~powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe -m unittest tests.test_mm -v
~~~

已完成完整离线回归与 Mid/Grid 真实行情纸面冒烟测试。没有使用真实账户下单验证；生产签名验收、成交、撤单及平仓表现仍需小资金测试确认。

## ?????????

?? `C:\Python312\python.exe -m arcus_mm run --dashboard`?????? http://127.0.0.1:8765 ?????????????????????????????????????????????????? `--flatten-on-exit` ????????? reduce-only ???`--port 8766` ??????????????????????????

???????? 8 ?????? TLS/????????? RTT ?? `clock_max_rtt_ms` ?????????? 5000 ms????????????????????????????????????????????????????

## Quote refresh optimization

`reprice_bps` defaults to `0.5`: replace on a price deviation greater than max(one tick, resting price * 0.5 / 10000), about USD 4.27 at BTC USD 85,400. Reduce-only changes, removed targets and position limits bypass this price threshold.

Periodic clock sampling runs in the background while the previous synchronization is valid (under 45 seconds). Healthy refreshes preserve resting orders; no valid samples pause quotes and cancel orders. Skew violations or local clock jumps still stop the session. Refresh tasks are cancelled during shutdown. `quote_cancel` records the slot, reason and old/new prices.

Restart the running process through its normal Stop control to load these Python/config changes. Never delete a live runtime lock to start a second process.

## BTC / ETH paper comparison

Run `C:\Python312\python.exe -m arcus_mm.compare --flatten-on-exit` or double-click `run_compare.bat`. The dashboard is http://127.0.0.1:8765 . Each market has its own USD 100 paper ledger and log directory (`mm_logs/BTC-USD`, `mm_logs/ETH-USD`). Both use the same spread and risk settings from `mm_config.json`. The comparison runner has no live option and never creates a signer. Its global runtime lock prevents concurrent maker sessions. One market stopping does not restart it or stop the other; the dashboard Stop button requests normal cleanup for both. Optional `--seconds 3600` limits the run to one hour.

## Local live credentials

Fill `.env` in the project root using `.env.example` as the template. Only `--live` loads this file. Existing process environment variables take precedence; empty template values are ignored. Use the registered Ed25519 API seed, not the Ethereum wallet private key. `.env` and `.env.*` are ignored by version control except `.env.example`.

Read-only account check: `C:\Python312\python.exe -m arcus_mm doctor --config mm_live_btc_100.json --live`.

## Confirming uncertain live requests

Transport failures and HTTP 5xx for place/cancel now first verify the original order through WebSocket state or REST `/v1/order/{orderId}`, followed by account/fill reconciliation. REST polling also works when the WebSocket is connected. Only a confirmed result and reconciled account permit continuation. No mutation is resent automatically; unknown placements retain exposure and stop. Unverified protection changes still stop. Structured `mutation_uncertain`, `mutation_recovered` and `mutation_recovery_failed` logs include action and exception type without credentials.

Explicit live recovery uses `--resume-log <prior live JSONL>` together with `--live`. The read-only doctor supports this option first. Recovery requires no open orders, a matching market/subaccount, a complete fill replay (under 1000 fills) and exact position/sequence reconciliation. Existing cost basis, fees and volume are replayed; risk stops are not automatically restarted. Connection/pool failures before HTTP transmission get one retry; read/write failures require original-request confirmation.


## Automatic live position recovery

The one-click launcher enables `--auto-resume`. A flat account starts a new session. For an existing BTC position, startup selects the latest complete live log for the configured market/subaccount, replays fills and verifies the exact account position and sequence before trading. Cost basis, fees, volume and the original risk capital are preserved. Failed startup/doctor logs are skipped. Existing open orders, positions in other markets, missing logs, over 1000 fills or reconciliation mismatches still prevent startup. The read-only `--check` uses the same recovery checks without sending mutations. This option does not automatically restart a running or stopped process or remove runtime locks.


## Live monitoring

The live launcher runs without a local HTTP dashboard. Runtime activity is written to the configured JSONL log directory. Use Ctrl-C in the launcher window to request normal cleanup and reduce-only flattening, then verify the final account position.


## Transient failure restart

The one-click launcher uses `--auto-restart --auto-resume`. Transient disconnects and rate-limit exits retry after 15, 30 and up to 60 seconds. Every new attempt must pass account/fill reconciliation and requires no open orders; runtime locks are never removed to force a restart. Operator stops (including the dashboard), stop-loss and other risk stops do not restart. Ctrl+C during backoff stops the restart loop. HTTP 429 pauses new requests for at least the server cooldown and does not count as an order failure; cleanup cancellation can wait up to 60 seconds for rate limits. Unknown mutation results remain terminal. Reload the one-click launcher to enable this behavior.


Startup protection rate limits are handled within the same session: wait for cooldown, then retry arming protection without repeating an already-confirmed leverage change. No quote cycle runs before protection succeeds. The dashboard shows a paused state until trading is enabled and the protection deadline is current; market connectivity alone does not imply that orders are being sent.


The exchange can reject `scheduleCancel` with `schedule cancel trigger limit reached (10 per UTC day)`. This is the daily auto-fire quota, not an ordinary short cooldown. The bot defers only protection re-arming until the next server UTC day (08:00 China time), plus five seconds, and keeps quotes disabled. Account reconciliation and floating-loss checks continue while activation waits; cancellation and reduce-only operator/stop-loss exits are not blocked by the daily protection cooldown. The dashboard shows the quota reason and next attempt time. Successful re-arming is still required before trading resumes.


## Protection and restart timing

Local disconnect handling remains 5 seconds in the live configuration. Server `protection_seconds` is independently set to 180 seconds and renewed every `protection_refresh_seconds` = 30 seconds. This leaves room for the maximum 60-second restart backoff and normal startup. A hard crash can leave resting orders until the longer server deadline; the local watchdog still attempts immediate cancellation at its 5-second limit.

After shutdown cancellation, fill/account reconciliation and REST confirmation of no open orders, the bot disarms protection only if every tracked order is terminal and there is no uncertain mutation/fault. Failed cleanup or uncertain order results leave the server fallback armed. This avoids consuming an auto-fire for ordinary clean exits. Quota already spent today is unaffected and still waits for the next UTC day. The dashboard shows the renewal interval and remaining server deadline.


## Software-only protection (current live launcher)

At the user's request, `mm_live_btc_100.json` now sets `server_protection: false`. Live startup confirms leverage and account consistency without arming or renewing `scheduleCancel`; the daily server auto-fire quota no longer gates trading. The software watchdog stops new orders and independently requests REST cancellation when its local fault thresholds trip. Cancellation retries definite pre-transmission failures and HTTP 429 for up to 60 seconds; uncertain mutations are not blindly resent. Stop-loss, position caps, authenticated Stop, startup account checks and restart recovery remain enabled. The dashboard explicitly labels software-only mode. A stopped/crashed process or a network outage affecting REST cannot guarantee cancellation; no server fallback exists in this mode. Re-enable `server_protection: true` to use server protection again. Restart normally to load these changes.


## Inventory-aware adaptive pricing

The live BTC configuration keeps leverage at 5, per-order notional at 10% of risk equity and the position cap at 25%. Inventory shifts the quote center by up to 2 bps against the held position (long shifts down, short shifts up). Directional budgets, minimum sizes and reduce-only handling at the cap still apply.

Fresh BBO mid prices are sampled at most once per second in a bounded 30-second window. After five samples, the RMS sampled return times 2, current half-book spread and the base 2 bps determine the half-spread, capped at 8 bps. This is a sampled price-change measure, not a forecast. Repricing scales from 0.5 to 2 bps; grid uses one level once the spread reaches twice the base. A fee floor covers positive maker fees plus 25% of the current taker fee; rebates do not narrow quotes. The fee floor can override the spread cap. This configurable reserve does not guarantee profitable fills. Funding data is not incorporated yet.

Paper and live engines share the pricing path. Default paper settings retain fixed quotes for baseline comparisons; use a copy of the live configuration without --live to test adaptive settings. quote_policy events record spread, reprice threshold, sampled volatility and fee floor. No benchmark improvement has been established. Restart normally to load these settings.


## BTC inventory-skew paper A/B

Run `python -m arcus_mm.ab --seconds 3600`. This command has no live option, never loads credentials, and forces paper BTC mid mode even when reading the live settings. Two independent ledgers consume the same public BBO/trade stream with identical fee, latency, adaptive-spread and risk settings; only inventory skew differs (0 versus configured 2 bps). It uses the existing exclusive runtime lock, so stop the current bot normally first.

Each variant writes its own JSONL under a timestamped mm_logs/ab-BTC-USD directory; summary.json contains wear_per_dollar, cancellations per USD 1000 of maker volume, and median inventory half-life. Half-life starts at a nonzero exposure, resets/censors if exposure increases, and completes when magnitude halves or the position crosses zero; pending intervals are censored. Only the latest 10000 completed intervals are retained. No fills means unavailable metrics, not zero risk. Duration completion and Ctrl+C request paper-only flattening with the same exit model. This does not validate live queue position or profitability.


## Pricing monitoring and offline hourly reports

The dashboard includes effective quote half-spread, replacement threshold, inventory center shift, sampled volatility, fee floor, maker volume share, net PnL after fees, and unconfirmed order count. Funding is excluded.

Use `python -m arcus_mm.report path/to/paper-or-live.jsonl --output mm_logs/hourly.json` for an offline UTC-hour report. Hourly fill totals include notional and actual recorded fees; maker share and cancellation request ratios are unavailable without maker fills. The last observed PnL/wear remain cumulative session figures, not hourly profit. Recorded disconnect, stop, reconciliation, clock and protection faults are counted as alerts; no messages are sent externally. Only selected statistical fields are output. Malformed or incomplete records are counted and skipped.


## Confirmed startup cleanup of bot orders

Run --live --auto-resume now permits cleanup of leftover bot orders before quoting. The latest matching live session must prove every open order by client ID; all must belong to the configured market. The engine cancels each proven order once, verifies its terminal status by REST, confirms the account has no remaining orders, then reloads the account and replays fills from the original recovery origin. Fills racing cancellation must reconcile before quotes begin. Manual/unlogged orders, other-market orders, missing logs, and unknown mutation results block startup. Doctor remains read-only and never cancels. A plain open-order startup refusal is no longer treated as transient, avoiding an endless restart loop. No live cleanup is performed merely by installing this update; restart the launcher normally to use it.


## Current live fill-rate tuning

The live configuration now uses a 1.2 bps base half-spread, a 4 bps adaptive cap, and a 1.2 volatility multiplier. The replacement threshold starts at 0.8 bps and scales up to 1.5 bps to reduce queue churn. Leverage remains 5, notional per order 10% of risk equity, position cap 25%, and floating stop 1%. The fee floor remains active. These settings seek more passive fills and do not guarantee higher volume or PnL.

Inventory-shifted reducing quotes that would cross the opposite BBO are clamped to the passive same-side BBO instead of disappearing. batch_quote_cancels allows both outdated sides to be cancelled sequentially with confirmation in one cycle; placements wait for the next cycle and recompute from current fills and prices. No cancel/placement requests are sent in parallel. Restart normally to load this profile.


## Request pressure and rejected cancellation

Signed writes are serialized and spaced by at least mutation_interval_seconds (default 0.3), with fresh timestamps after pacing. Exhausted account-pool snapshots add a conservative two-second pool delay; the -1 sentinel is not treated as exhaustion. Consecutive HTTP 429 responses back off for 5, 10, 20, 40 and 60 seconds, respecting any longer server delay. The streak resets after 120 seconds without a limit. Read-only 429 also pauses new writes instead of becoming a generic consecutive failure.

403 is not blindly treated as transient. Only an explicit JSON temporary-ban/rate-limit message enters cooldown (at least 30 seconds); other 403 responses stop with a classified forbidden reason, and the same write action is not retried in that process. No gateway restriction is bypassed. Confirmed cleanup is reused only while the tracked order revision remains unchanged and no tracked orders remain; new placements invalidate it. Unknown or forbidden cleanup results remain failures and are not resent by watchdog/main/finally paths. These controls reduce request pressure but cannot repair revoked keys, authorization mismatches or server bans. Verify residual orders/positions if cleanup fails.


## Current optimization progress

See [optimization progress](docs/OPTIMIZATION_PROGRESS.md) for completed cancellation recovery, Microprice/L1 features, cubic inventory controls, maker markouts, side-specific toxicity spreads, multi-scale EWMA, validation results and remaining work. This document supersedes older cancellation policy notes: unclassified 403 is not permanent permission failure; cleanup still requires terminal-order proof.

Full offline checks: `python run_offline_tests.py -v` (external network blocked, loopback dashboard tests allowed). See [code review](docs/CODE_REVIEW_2026-10-07.md) for the latest fixes and validation.

Unclassified placeOrder 403 stops can restart after verified cleanup and account/fill reconciliation, with a minimum configured cooldown (default 60s, rejection_restart_seconds). This requires auto-resume; operator stops, risk stops, explicit permission failures, uncertain orders and failed cleanup remain final.
