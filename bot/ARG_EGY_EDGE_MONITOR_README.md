# 阿根廷 vs 埃及比分盘 edge 监控

注意：根目录 `README.md` 保持项目主说明；这个文件才是 `edge监控` 分支里针对实时 edge 监控程序的专用说明。

这个文档只解释 `arg_egy_exact_score_edge_monitor.py` 这个实时监控程序。

它不是交易引擎，不会下单，也不会自动 convert/merge。它只做一件事：持续监控指定 Polymarket 多比分市场，计算“买齐所有 NO 后是否有 full-set convert 套利空间”。

## 当前监控对象

默认市场：

`Argentina vs. Egypt - Exact Score`

默认事件 slug：

`fifwc-arg-egy-2026-07-07-exact-score`

这个市场有 17 条比分腿，包括 `Any Other Score`。如果买齐每一条腿的 NO，理论上可以通过 full-set convert 收回：

`17 - 1 = 16`

## 核心计算逻辑

每 5 秒拉一次所有比分腿的订单簿，然后计算：

```text
edge = 收回金额 - 全部有效 NO 价格和 - taker 手续费 - gas buffer
```

当前默认 gas buffer 是 `0`，所以实际是：

```text
edge = 16 - sum(effective_no_price_i) - sum(taker_fee_i)
```

如果 edge 大于 0，说明从盘口快照看，买齐所有 NO 后扣除 taker 费仍有套利空间。

如果 edge 小于 0，说明买齐后 convert 会亏。

## 有效 NO 价格

每条比分腿会同时看两种 taker 可用价格：

1. 直接 NO 卖价：有人挂单卖 NO，你直接买 NO。
2. YES 买价折算：有人买 YES，对应的等价 NO 价格是 `1 - YES 买价`。

程序取两者中更便宜的价格作为这条腿的有效 NO 价格。

如果两者价格一样，程序只把它当作同一档有效价格，不把深度重复相加。

## taker 手续费

程序会对每条腿从 CLOB 市场信息里读取费率参数，然后按价格计算每股 taker 费：

```text
fee = fee_rate * (price * (1 - price)) ^ fee_exp
```

每条腿的手续费加总后，从 edge 里扣除。

## 页面展示

启动后，本地页面地址是：

`http://127.0.0.1:5188/`

页面包含：

- 最新 edge
- 全部 NO 价格和
- taker 手续费和
- full-set convert 理论收回金额
- 最新 tick 时间
- edge 历史曲线
- 最新每条比分腿的盘口明细

曲线特点：

- 每 5 秒记录一个 edge 点。
- 启动时会从 `arg_egy_exact_score_edge.jsonl` 回灌历史数据。
- 最多展示最近 30000 条记录。
- 横轴展示 5 个均匀分布的时间刻度。
- 鼠标移到曲线上，会显示最近真实采样点的时间、edge、NO 价格和、手续费。
- 曲线是平滑线，不突出每个采样点。

## 运行方式

在项目根目录运行：

```powershell
python bot\arg_egy_exact_score_edge_monitor.py --port 5188 --interval 5 --workers 24 --gas-buffer 0 --max-points 30000
```

常用参数：

```text
--slug         指定 Polymarket 事件 slug
--interval     监控间隔，默认 5 秒
--port         本地页面端口，默认 5188
--workers      并发拉盘口的线程数
--gas-buffer   每套额外扣除的 gas 缓冲
--max-points   页面内存里保留的历史点数量
--log          JSONL 日志路径
```

## 日志文件

默认日志：

`bot/arg_egy_exact_score_edge.jsonl`

每一行是一条 5 秒采样记录，包含：

- 时间
- 是否所有腿都有有效 NO 报价
- 是否所有腿都有直接 NO 卖单
- 收回金额
- NO 价格和
- taker 手续费和
- edge
- 最新每条腿盘口明细

## 停止方式

如果知道进程 PID：

```powershell
Stop-Process -Id <PID>
```

也可以查找当前监控进程：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*arg_egy_exact_score_edge_monitor.py*' }
```

## 当前边界

这个程序只负责监控和可视化，不负责：

- 自动下单
- 自动撤单
- 自动 convert
- 自动 merge
- 部分成交后的库存管理
- 多市场自动选择

它的作用是先验证一个具体市场在实时盘口下是否出现过“全套 NO taker 买入后费后正 edge”的窗口。
