# 当前任务：100 美元 BTC/ETH 合约模拟

默认交易入口已切换至合约模拟，详情见 [PERPS.md](PERPS.md)。
运行 `python arcus.py compare` 比较市场，`python arcus.py dry --seconds 3600` 模拟。
实盘适配未验证，live 会阻断。下面是旧 NVDA 现货文档，旧命令需加 spot 前缀。

---
# Arcus NVDA Spot 实盘适配

当前配置：只开 NVDA/USDG 现货仓。本金 100 USDG、单笔 10 USDG、日亏损触发线 5 USDG、每日轮次不设上限。滑点 30 bp、额外缓冲 3 bp、单笔止损 40 bp、超时 20 分钟；连续三次报价失败或三次止损/超时禁止新仓。默认 DRY_RUN。

**实盘路径已经写入代码，但尚未用用户钱包完成链上成交验证。没有发起授权、签名或交易。**

## 已确认的真实接口

官方协议来源：[Spot Router OpenAPI](https://router.spot.arcus.xyz/openapi.json)。不使用 Perps API、placeOrder 或存入合约账户的接口。

|接口|真实字段及用途|
|---|---|
|GET /v1/tokens?chainId=4663|顶层数组，symbol/address/decimals/wrappedTokenAddress；运行时获取|
|GET /v1/deployment|chainId、contracts.permit2、arcusSettlement、swapShell|
|GET /v1/price|请求 sellToken/buyToken/sellAmount（整数最小单位）；响应 all[].buyAmount/sellAmount/fees|
|GET /v1/quote|增加 taker、slippageBps=30、allowWrapped=false、sourceInclude=arcus；响应 all[].expiry、toSign、arcus.minAmountOut|
|钱包 EIP-712|Permit2 / PermitWitnessTransferFrom / TakerIntent；签署真实返回的 toSign|
|POST /v1/submit|venue=arcus、chainId、taker、signature、typedData、buyToken、builderFeeBps=0；返回 submitted/txHash|
|GET /v1/status|venue=arcus、id=txHash、chainId；pending/confirmed/failed/unknown，成功后有 swap|
|RH JSON-RPC|chainId、ERC20 余额/allowance/decimals、ETH、回执和 Transfer 日志|

签名前逐项校验 domain、类型定义、链、Permit2、spender、钱包、NVDA/USDG 交易对、数量、滑点下限、nonce、deadline 和禁止 wrapped 的约束。只接受普通 EOA，暂不支持 EIP-7702/智能钱包。

官方公共 RPC：https://rpc.mainnet.chain.robinhood.com；2026-09-29 联调已返回 0x1237（4663）。可用 ARCUS_RPC_URL 覆盖。[官方网络说明](https://docs.robinhood.com/chain/connecting/)

## 费用事实与当前阻断

2026-09-29 成功 quote 实测出现 protocol fee **5 bp**，还有以 USDG 计价的 gas 费用。与早先“平台费 0、gas 只从单独 ETH 支付”的约束冲突。

`accept_quote_fees=false` 保留原要求，检测到非零报价费用就禁止开仓。必须先由用户接受真实费用才改 true。quote 中 buyAmount 作为报价输出，最终以回执与钱包余额差核实净到账；不要重复扣减已包含的报价费用。

美东盘前的一次只读样本：10 USDG → 0.043146011673963744 NVDA → 9.932690 USDG，预计损耗 67.31 bp。仅为短时公开接口验证，使用官方文档示例公开地址，**不是用户成交，不是开盘校准，不用于设置 ROUND_TRIP_BPS**。原始样本位于 research/round_trip_probe.json。

正常开仓另检查是否预期立即触发 40 bp 止损，若往返损耗已达到止损线则跳过。校准模式立即反手，单独使用明确配置的 calibration_max_loss_bps。

## 文件

- spot_api.py：真实报价解析、钱包环境变量签名、RPC、余额/授权、回执 Transfer 解析。
- spot_runtime.py：实盘与模拟运行、状态恢复、有限额度 approve、小额立即反手校准。
- arcus.py：保留旧骨架及 tokens/capture，并转发新增命令。
- config.json：风控；calendar.json：核实过的美股日历与 NVDA 财报排除。
- fixtures/：来自真实成功 GET 的公开测试样本，永不用于提交。
- live_state.json：本金账、仓位、pending、每日亏损、真实校准样本与权威成交记录。
- live_trades.jsonl：便于阅读的成交日志；崩溃后以状态文件里的 trades 为准。

## 本机配置（不要把秘密发到聊天）

进程环境变量：

|名称|用途|
|---|---|
|ARCUS_WALLET_ADDRESS|要交易的公开 0x 地址，必须与签名账户一致|
|ARCUS_PRIVATE_KEY 或 ARCUS_MNEMONIC|仅在本机环境中设置其一；不会打印|
|ARCUS_HD_PATH|使用助记词时的派生路径；默认 m/44'/60'/0'/0/0|
|ARCUS_RPC_URL|可选 HTTPS RPC；未设置使用官方公共节点|

默认额外 ETH 储备 min_eth_balance=0.0001 ETH，不计入 100 USDG 交易本金；approve 还要求支付 gas 后仍达到储备。现有持仓退出不因 ETH 储备低于该值被阻止，但仍校验余额和授权。若真实报价存在 USDG gas 费用，其处理由上述费用策略控制。

calendar.json 的 sessions 按美东日期填写：verified=true、带时区偏移的 open_et/close_et、earnings_clear_symbols 包含 NVDA。只填已核实且非 NVDA 财报日的正常/提前收盘交易日；未知日历关闭开仓。开盘后等 10 分钟，收盘前最后 20 分钟不开新仓。

## 联调与启动顺序

```powershell
python -m pip install -r requirements.txt
python -m unittest -v
python arcus.py doctor
python arcus.py tokens
python arcus.py preflight
python arcus.py watch --count 3
```

preflight 只读核对链、余额与 pending。watch 使用真实钱包地址报价、记录双向预计损耗，不签名不提交。当前尚无钱包环境变量时会给出明确 BLOCKED。

先处理费用选择并核实交易日历。根据开盘后的 watch 实测设置以下临时门槛，不使用这里的盘前样本猜值：

- dry_run_round_trip_bps：仅模拟盯盘使用的明确临时阈值。
- calibration_max_loss_bps：3–5 笔真实立即反手校准允许的最大预估往返损耗。
- round_trip_bps：旧骨架兼容字段；**新实盘运行器自动由 live_state.json 的当天真实校准成交均值确定，不用模拟样本**。

```powershell
python arcus.py dry
```

dry 仍核对钱包真实余额，但成交只更新独立的 dry_spot_state.json，绝不调用签名或 submit。正常 target 出场还要求最新卖出 quote 的最低回款覆盖目标。

**授权准备：**买前要求 USDG 和 NVDA 对官方 Permit2 已有足够 allowance。若不足，使用明确金额授权（不无限授权）：

```powershell
python arcus.py approve --token USDG --amount 10
# NVDA 金额填写已核实的退出需求量；不在文档猜固定数值
python arcus.py approve --token NVDA --amount <所需NVDA数量>
```

approve 会打印资产、数量、spender 和 ETH 成本上限，并发起真实有限额度授权。再次执行 approve 会先核对已有 approval_pending.json 回执而不重复广播；交易前必须清除已确认的授权 pending。后续 allowance 消耗不足会停止交易，需补足有限授权。有持仓时仅允许不超过该仓位数量的 NVDA 退出授权。

```powershell
python arcus.py calibrate --rounds 3
python arcus.py live
```

这两个命令是**明确实盘入口**，会签名和提交；它们显式覆盖默认 DRY_RUN。不要在只读联调阶段执行。calibrate 要求当日同钱包 watch 记录、核实的时段、费用策略、授权和校准损耗上限，单笔仍为 10 USDG。校准成交损耗计入同一个 5 USDG 日损耗账，正常 live 不重置。测满 3–5 笔才允许正常策略开仓。

## pending 与恢复

每次 POST 前原子保存 pending。submit 超时、响应异常或状态未知，不重新 POST、不另开仓。持仓不会用报价数量冒充实际成交量；必须同时满足成功状态、确认回执、Transfer 净额和钱包余额差。

```powershell
python arcus.py recover
# 如果 submit 响应丢失，先从实际链上记录找到真实 txHash，再提供：
python arcus.py recover --tx-hash <真实交易哈希>
```

recover 只处理已有 pending，不能替换已有 txHash，不会重发。缺少 txHash 时始终阻断。恢复成交仍须匹配原 signed pair、数量、最低回款、结算地址和余额差。失败或未知状态不会被当成功。

风控和配置快照盘中冻结；已有持仓且配置被改时继续用旧参数、只允许退出。跨日不自动清零，不会因此绕过日亏损，需要先核对平仓及 pending 后归档状态、重新做当日校准。不自动日切或自动解除 halt，禁止靠删除状态绕过限制。进程崩溃遗留 spot.lock 时先确认无存活进程和查明 pending 再处理锁。

5 USDG 是停止开仓/尝试平仓的触发线，网络或流动性故障无法保证最终损失绝不超过它。该程序不加仓回本、不切换合约、不使用网格。


## 启动检查和跨日归档

`python arcus.py doctor` 不联网、不读取密钥内容，只报告环境变量是否配置、风控、当日日历、校准、费用选择及 pending 的就绪情况。它不代表余额或链上状态已经验证，继续运行 preflight 才会核对真实链上数据。

下一美东交易日开始前使用 `python arcus.py new-day`：只允许旧状态日期早于今天、全部平仓、无交易/授权 pending、钱包余额与旧账一致、ETH 储备充足。归档保存至 state_archive/，现金沿用真实剩余本金，日亏损与轮次清零，但校准也清空，必须重新测 3–5 笔。不能在同一天运行此命令绕过 5 USDG 风控。若还有仓位或 pending，先恢复并退出，再归档。此命令不会下单。
