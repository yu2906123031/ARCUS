# Arcus BTC/ETH 合约模拟（2026-10-06）

当前默认 `doctor / probe / watch / dry / live / preflight` 已转到合约模块。
实盘执行器尚未完成验证，`live` 和 `preflight` 明确阻断，绝不会落回现货下单。

```powershell
python arcus.py compare
python arcus.py doctor
python arcus.py dry --seconds 3600
```

先比较 BTC、ETH，再用 BTC 模拟。仅公共 GET，无签名、授权、充值或下单。
旧 NVDA 现货入口用 `python arcus.py spot <command>`，沿用 config.json。
合约配置是 perps_config.json，与旧现货状态隔离。

资金 100 USD，最高有效杠杆 3x，名义仓位上限 285 USD，保留 5 USD 损失预算。
亏损后仓位随权益降低；单次净亏损 1 USD 触发模拟止损，累计亏损 5 USD 或连续三次非止盈退出停止开仓。
损失预算不因盈利或跨日重置。轮次无上限，但只在 30 秒窗口价格变化达到 2 bp 且往返成本不超过 8 bp 时开仓。
这只是待验证的动量基线，并没有被证明能实现盈利或最大成交量。

盘口按真实深度加权，额外每边预留 1 bp 滑点；按实时基础 Taker 费率计两边费用，完全不假设零费 Maker 成交。
正常止盈要求：可执行退出价对应的净 PnL（双边手续费、滑点、资金费预留均扣除） >= 名义金额的 1 bp。
止损、最长持仓 20 分钟和观察期结束退出可以亏损；保本门槛不是保本承诺。
资金费采用持仓内观察到的最大绝对费率，每经过一小时加一个区间并额外预留一区间，是保守估算，并非实际结算。
`--seconds` 到期禁止新仓，并将现有模拟持仓按退出盘口结束观察。

模拟输出：perps_paper_state.json；市场比较：research/perps_comparison.json。
只累计模拟开平仓金额，real_volume_usd 始终为 0，不把模拟量当实际成交量。
新运行会沿用持仓和亏损账本；已有停止标记不自动清除。修改参数前需归档模拟状态，不能覆盖实盘状态。
网络失败、盘口过期、深度不足或字段异常时停止模拟，不编造成交。

实盘准备还需要：本地 API Signing Key、公开钱包地址、子账户索引、实际合约账户权益及费率。
密钥不要发到聊天；签名与 API Key 注册使用 Arcus 官方 Ed25519/EIP-712 规范。
实盘适配器需要 WebSocket orders/userFills、部分成交和撤单竞态处理、提交前持久化 intent、
重启恢复与账户/持仓/实际手续费/资金费对账、reduceOnly 退出、服务端止损和断线取消。
REST 接收回执不是成交，不能用 200/202 HTTP 响应计交易量。

官方接口文档：
- https://docs.arcus.xyz/guides/rest-trading
- https://docs.arcus.xyz/api-reference/public/get-fee-tier-table
- https://docs.arcus.xyz/api-reference/public/get-l2-orderbook-snapshot
- https://api.arcus.xyz/v1/markets

2026-10-06 只读样本：BTC 24h 约 2.72 亿 USD，ETH 约 4108 万 USD。
基础 Maker 0、Taker 225 ppm（单边 2.25 bp）。285 USD 名义仓位双边费用约 0.128 USD，
加盘口与每边 1 bp 滑点后，BTC 立即往返损耗约 0.186 USD。
行情随时变化，运行 compare 获取新样本。
