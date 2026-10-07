# Arcus 官方协议核对

核对日期：2026-10-06。参考仓库仅阅读 README 流程，未获取或复制源码。官方文档缓存位于 research/。docs.arcus.xyz 在本环境证书过期且代理返回 403，最终从证书有效的官方部署 https://arcus-11aecf6e.mintlify.app 获取同一文档。交易代码始终验证 TLS。旧 perps_openapi.yml 实际是 404 HTML，不作为规范。

## 签名

依据 [Authentication](https://docs.arcus.xyz/api-reference/authentication)、[placeOrder](https://docs.arcus.xyz/api-reference/exchange/place-order)、[cancelOrder](https://docs.arcus.xyz/api-reference/exchange/cancel-order)。

REST 变更带 X-API-Key（64 hex 公钥）、X-Timestamp（纳秒十进制）、X-Signature（128 hex 签名）及 ?address=。下单 body timestamp、clientTime 与签名 ct 和头时间一致。私钥只读环境变量。

placeOrder 签紧凑、排序、UTF-8 JSON，无 timestamp/action 前缀：

~~~json
{"ad":"0x...","ai":0,"c":"clientId","ct":123,"g":2000000000000000000,"m":1,"op":1,"p":500001,"q":12345,"r":0,"s":0,"t":3,"v":1}
~~~

| 字段 | 官方含义 |
|---|---|
| ad | 所属地址，唯一转小写的字符串 |
| ai | 子账户索引 |
| c | clientId，空值完全省略，保留大小写 |
| ct | Unix 纳秒整数，等于请求头 |
| g | HTTP goodTilTime 微秒字符串 ×1000，纳秒整数 |
| m | marketId |
| op | 1 下单，2 撤单 |
| p | price / 基础 tickSize，精确整数 |
| q | quantity / stepSize，精确整数 |
| r | reduceOnly，0/1 整数，不是布尔值 |
| s | BUY=0，SELL=1 |
| t | GTT=0，FOK=1，IOC=2，ALO=3 |
| v | 1 |

cancelOrder 签 ad,ai,ct,m,op=2,v=1，另加且仅加一个 id 或 c；不签价格数量。HTTP body 用官方 kind=clientId 或 orderId；ID 大小写保持原样。

setLeverage、cancelAllOrders、scheduleCancel 用 scheme 2：decimal_timestamp + camelCase_action + compact_sorted_json(body)，无分隔符，不包含 HTTP method。不要将 scheme 2 用于下单或单笔撤单。实现未使用 modify/batch；通用 OpenAPI 摘要与认证页在 modify 条件字段上存在差异，范围不含此操作，因此未引入该歧义。

包括 IOC 在内的每笔订单必须指定至少一个月后的 goodTilTime，程序使用 40 天。以认证页明确要求覆盖短期 GTT 示例。做市 LIMIT/ALO；止损 LIMIT/IOC/reduceOnly=true，以限价控制滑点。不能依靠短 GTT 或断线自动撤单。

## 行情与回报

依据 [WebSocket](https://docs.arcus.xyz/api-reference/websocket)、[BBO](https://docs.arcus.xyz/api-reference/market-data/bbo)、[trades](https://docs.arcus.xyz/api-reference/market-data/trades)。

- 主网 REST https://api.arcus.xyz；WS wss://api.arcus.xyz/v1/ws。
- 订阅 {"type":"subscribe","channel":"bbo","id":"BTC-USD"}；trades 同形。
- subscribed 是快照，channel_data 是更新；trades 仅流式更新。
- BBO contents 含 bestBid/bestAsk 的 price/size，timestamp 为微秒。
- trades contents 是数组，包含 price/size/tradeId/timestamp（微秒）；没有 takerSide，代码不擅自假设。
- orders/userFills 按地址 id、accountIndex、market 订阅。账户频道公开可读，不需要 authenticate；写操作逐请求签名。
- HTTP 202 仅 ACK，200 也不替代完整生命周期；确认终态后释放订单预算。
- /v1/account positions 为 marketId 到 position 的映射，size 为有符号数量。账户、成交账本及 sequence 一致前不开单。
- /v1/fills 的 from/to 为微秒，边界包含；使用重叠窗口与 tradeId/orderId/side 去重。窗口达到 1000 条就停止，不假定截断结果完整。
- /v1/time 的 timeNs 为纳秒，官方认证容差 ±30 秒。默认偏差上限 5 秒，加计 RTT/2，仅检查通过后校正签名的小偏移。

## 风控接口及精度

依据 [scheduleCancel](https://docs.arcus.xyz/api-reference/exchange/schedule-cancel-all-dead-mans-switch)、[get markets](https://docs.arcus.xyz/api-reference/public/get-markets)、[set leverage](https://docs.arcus.xyz/api-reference/exchange/set-leverage-for-a-market)。

scheduleCancel 的 time 是绝对微秒，提前 5 秒–5 分钟；marketId 限定 BTC；按 scheme 2 签名。断线本身不会撤单，该保护只撤单不平仓。每日自动触发等配额以最新官方文档为准。退出不解除后备开关。

tickTiers 的增量字段是 tick，upToPrice 是不包含的上界；签名仍除以基础 tickSize。市场不 ONLINE 或精度变化时停止。杠杆设置为配置值并通过 /v1/leverages 确认，默认及上限 3，不采用市场最大杠杆。

时钟修复：维持原 5 秒阈值，每轮获取多个样本，丢弃超时和高 RTT 响应并采用最快有效样本。全部样本无效时暂停开新单并撤单，恢复可靠采样后继续；持续无效按断线时限停止。检测本地 wall clock 与 monotonic 时间差跳变时立即停止。此为客户端测量改进，不改变官方签名协议。
