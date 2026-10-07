# 运行日志停机诊断

日期：2026-10-07。分析目录：mm_logs/live-BTC-USD。

## 最近几次实际停机

| 会话 | 首个可见停机原因 | 结论 |
| --- | --- | --- |
| 18:11:23 启动，18:12:58 退出 | fatal / TypeError | 最新问题是未处理的运行时异常；无 halt、final 或明确撤单完成事件 |
| 17:51:20 启动 | setLeverage 未分类 403 两次 | 旧代码重复设置杠杆；18:11 会话已出现 leverage_adopted，证明先读后写修复生效 |
| 16:55:21 启动 | cancelAllOrders 429 后未分类 403 | 旧的撤单拒绝永久升级路径；不应与最新 TypeError 混为一个问题 |
| 16:54:46 启动 | disconnected, stale data, clock or incomplete subscriptions | 本地连接/行情/校时/订阅保护停机 |
| 16:18:30 启动 | 断线后撤单连续 429、403 | 旧取消池退避和拒绝处理触发停机 |
| 16:17 之前多次启动 | startup requires no open orders | 遗留挂单阻止启动，属于当时的启动核对条件 |

以上时间为本地北京时间。撤单、杠杆和运行时异常是不同问题，不宜通过统一扩大失败阈值或无限重启处理。

## 最新会话的证据

18:11:35 已确认杠杆接管并启用交易。之后有正常 ALO 下单、订单终态、成交和 markout 记录；POST_ONLY_WOULD_CROSS 按报价重试处理，并没有直接停机。

18:12:53 的账户核对净仓为 0.00011879 BTC；18:12:55 又成交 BUY 0.00011891 BTC，账本净仓变为 0.00023770 BTC。随后 markout 记录 session_closed，18:12:58 写出 TypeError。

此日志没有 traceback、原始异常文字或错误行。也没有 final / cancel_all_confirmed，因此不能仅凭该日志断言退出清理完成，更不能确定 TypeError 来自哪一条 API 响应或哪一行代码。

后续只读查询确认：openOrders 数量为 0，账户 BTC 净仓为 **0.00011883 BTC 多仓**。这是查询时的状态，不是完整复原崩溃时账户过程；查询没有发送交易或撤单请求。

## 本轮修复：让下次异常可定位

新增 diagnostics.exception_locations，只记录最后最多 12 个文件名、函数名和行号，不记录异常正文、源码行、局部变量、请求头或密钥。

- execution_error：在退出清理前记录原始运行异常的位置。
- cleanup_error：单独记录清理失败的位置与原始异常类型。
- fatal：也附带错误位置。
- 若原始运行异常与清理异常同时出现，保留原始异常，不再让后续清理覆盖最初故障。
- 报告将 execution_error / cleanup_error 纳入告警计数。
- 未将 TypeError 自动归为可恢复网络故障，避免在未知状态下反复启动。

新增三个离线回归测试，验证诊断信息不泄露正文、清理错误不覆盖原始错误、单独清理错误仍正常向上报告。

## 验证与下一步

全仓库 254 项离线测试通过（84.605 秒），新增诊断代码语法与 git diff --check 通过。测试日志为 mm_logs/stop-diagnostics-tests.txt。

正常重新启动后，如果再次退出，最新日志里的 execution_error.locations 和 cleanup_error.locations 可以提供准确的出错文件与行号。当前旧日志不能补回已丢失的调用栈，所以本轮没有宣称已修复尚未定位的 TypeError 根因。

实盘保持 5 倍；未启动机器人，也未对账户下单、撤单或平仓。改动尚未上传。


## 18:21 会话根因已确认并修复

新日志 live-20261007-182135.jsonl 提供了完整位置链：engine.cycle → engine.cancel_all → Live.cancel_all（api.py:742）→ Log.__call__（models.py:130）。运行异常与退出清理异常均落在同一位置。

实际缺陷：逐单清理日志传入 mode="individual"，日志器同时用 mode="live" 创建 dict，Python 抛出 TypeError: dict() got multiple values for keyword argument 'mode'。调用发生在逐单撤单之前，导致清理本身和退出清理重复崩溃。这是此前改动引入的日志字段冲突。

现已将业务字段改为 cleanup_mode="individual"；日志器用字典合并生成记录，固定元数据 time_ns / mode / event 由日志器赋值，不受调用者覆盖。同名 mode / time_ns 参数不再使记录创建崩溃。

新增真实 Log 集成测试，先复现相同调用栈，再验证两笔逐单撤单及终态清理完成；另外验证日志字段冲突不会覆盖实盘模式或时间戳。之前撤单测试使用空日志替身，遗漏了这一真实路径。

修复后全仓库 **256 项离线测试通过**（88.143 秒），专项 16 项通过；全部做市日志调用未发现其他 mode / time_ns / event 字段冲突，git diff --check 通过。测试结果：mm_logs/logger-fix-full-tests.txt。

本次根因修复覆盖该 TypeError；不把未知订单结果、权限错误和止损改成无限重启。正常重新启动后加载修复。
