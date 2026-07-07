# Polymarket Neg-Risk 扫描与 p3nny 研究

这个项目用于研究 Polymarket 多选项互斥事件里的 neg-risk 交易方式，重点观察 p3nny 类似账户如何通过买入多条 NO、转换、合并来获得套利边际。

当前代码不是完整自动交易系统，而是一个研究和测试框架：

- 扫描热门多选项事件。
- 拉取每条结果腿的订单簿。
- 计算买入全套 NO 后，扣除 taker 手续费和缓冲成本是否还有 edge。
- 实盘下单入口存在，但默认关闭，并且不会自动 convert 或 merge。

## 当前主引擎

主引擎文件：

```text
bot/p3nny_taker_engine.py
```

它现在做的是 **fee-aware full-set NO taker 扫描**。

通俗讲，就是：

1. 找到成交量较大的多选项互斥事件。
2. 对事件里的每一个结果，找到对应的 NO 代币。
3. 看每条 NO 当前有没有可直接吃的卖单。
4. 假设每条腿都用 taker 买同样数量的 NO。
5. 计算买齐后 full-set convert 理论上能收回多少钱。
6. 扣掉买入成本、taker 手续费和 gas 缓冲。
7. 如果剩下的钱为正，并且超过阈值，就输出候选机会。

## 市场选择逻辑

主引擎会从 Polymarket Gamma API 按 24 小时成交量从高到低拉事件。

保留的事件需要满足：

- 事件仍然活跃，没有关闭。
- 是多选项互斥事件，也就是支持 neg-risk 的事件。
- 24 小时成交量高于设置的最低门槛。
- 事件内部的子市场都是标准 Yes/No 二元市场。
- 子市场正在接受订单，并且有订单簿。
- CLOB 返回的信息确认该子市场支持 neg-risk。

默认参数比较保守：

```text
最低 24h 成交量: 5000
最多扫描事件数: 10
最多结果腿数: 18
每条腿买入数量: 1
最低费后 edge: 0.003 / set
```

如果使用 `--all-above-min-vol`，脚本会继续往下扫，直到遇到 24 小时成交量低于门槛的事件。

## 当前盘口判断逻辑

这里要特别注意：**当前主引擎只看直接 NO 卖单**。

也就是说，它判断一条腿“可以买 NO”的方式是：

```text
NO 订单簿里是否存在 ask
```

如果某条腿没有直接 NO 卖单，主引擎会认为这条腿当前不可买，整个 full-set 机会也不会成立。

当前主引擎还没有把下面这种等价流动性纳入扫描：

```text
有人买 YES，理论上可以折算成 1 - YES买价 的 NO 买入价格
```

所以主引擎是保守口径。它可能低估热门市场的可成交 NO 流动性。

## Edge 计算方式

对于一个有 N 条结果腿的事件，如果每条腿都买入 q 股 NO：

```text
理论收回现金 = (N - 1) * q
```

因为在一个 N 结果互斥事件里，买齐所有结果的 NO 后，最终一定只有一条 NO 归零，其余 N-1 条 NO 等价于现金回收。

当前主引擎按每一 set 计算：

```text
raw_cost = 所有 NO 的平均买入价格之和
fee_cost = 所有腿 taker 手续费之和
net_edge = (N - 1) - raw_cost - fee_cost - gas_buffer
```

如果 `net_edge` 小于阈值，就不会输出候选。

手续费按每条腿的成交价格动态估算：

```text
fee_per_share = fee_rate * (price * (1 - price)) ^ fee_exp
```

其中 `fee_rate` 和 `fee_exp` 来自 CLOB 市场信息。

## 子集转换观察

主引擎里还有一个 subset 纸面观察模式：

```text
--show-subset
```

它会从当前有 NO 报价的腿里选一部分便宜腿，计算如果只买这些 NO 并做 subset convert，现金部分是否为正。

但这只是观察，不是当前实盘策略。

原因是 subset convert 后还会收到一包 YES 暴露，这包 YES 需要后续 merge、卖出、持有到结算，或者用保守价值估算。当前主引擎没有完整处理这部分后续路径，所以 subset 默认不用于实盘下单。

## 运行示例

只扫描一次，不下单：

```bash
python bot/p3nny_taker_engine.py --once --events 10 --clip 1
```

连续监控 30 轮，不下单：

```bash
python bot/p3nny_taker_engine.py --cycles 30 --sleep 3 --events 10 --clip 1
```

扫描所有 24 小时成交量超过 1 万的事件：

```bash
python bot/p3nny_taker_engine.py --once --all-above-min-vol --min-vol 10000 --pages 80 --max-n 120 --clip 5
```

## 实盘边界

主引擎虽然有 live 参数，但默认不会下单。

实盘需要同时满足：

```text
--live --confirm-live YES
```

即使打开实盘，当前版本也有几个重要限制：

- 多条腿分别下 FOK 单，不是原子交易。
- 某些腿成交、某些腿失败时，不会自动回滚。
- 不会自动执行 convert。
- 不会自动执行 merge。
- 不会自动处理 subset 后收到的 YES 暴露。

所以当前 live 入口只适合极小资金、明确人工监控的管道测试，不适合直接放大运行。

## 当前已知问题

1. 主引擎的盘口判断仍然偏窄，只看直接 NO 卖单。
2. 主引擎尚未把 `1 - YES买价` 作为等价 NO 流动性纳入 full-set 扫描。
3. 主引擎没有实现自动 convert 和 merge。
4. 子集策略只做纸面观察，没有完整库存和 YES 后处理。
5. 多腿下单不是原子交易，实盘有残腿风险。
6. 本项目中的历史 JSON、日志和本地快照已通过 `.gitignore` 排除，不随代码仓库提交。

## 下一步

优先要做的不是扩大下单，而是把主引擎的盘口口径修正为：

```text
每条腿有效 NO 价格 = min(直接 NO 卖价, 1 - YES买价)
```

然后再做三件事：

1. 用 dry-run 长时间记录真实 edge 分布。
2. 验证 FOK 买 NO 是否能稳定吃到 YES 买单折算出来的流动性。
3. 在链上实现并验证 convert / merge 的完整闭环。
