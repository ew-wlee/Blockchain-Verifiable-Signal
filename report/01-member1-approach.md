# Member 1 思路：从固定信号到链上登记

> 这份文档只讲我在 Step 1–3 里怎么想、怎么做、为什么这么选，方便后面写报告和做答辩材料时直接取用。所有数字都来自仓库里的实物证据，没有估算。

## 一句话

把"我用某个模型在某段数据上算出了一组信号"这件事，变成"任何人都能拿同一份文件重算出同一组哈希，并与链上记录逐项对上"。

## 先想清楚边界

一开始最容易走偏的是把项目说成"链上跑模型"或"链上交易"。这两件事都不成立：

- 链上只存 4 个 `bytes32` 哈希、登记者和时间戳（`objectId` 只作键），不存 CSV、不存权重、不做推理。
- 回测只用来检查信号和成本规则是否自洽，不接真实资金，也不能当作收益承诺。
- 所以这套东西能回答的是"你看到的这份数据和信号，是不是当时登记的那一份"，不能回答"这份数据真实吗、模型准吗、策略赚钱吗"。

把边界先说死，后面每个设计选择才有判断标准：**凡是会破坏"可重算"的做法都不要。**

## Step 1：把输入钉死

### 固定窗口，而不是拉最新数据

数据用 Binance Spot 的 BTCUSDT/ETHUSDT 1h，窗口写死成 `2024-01-01` 到 `2025-01-01`（UTC），validation 是 `2024-07-01` 到 `2024-10-01`，test 是 `2024-10-01` 到 `2025-01-01`。

不用 `latest` 的原因很直接：只要窗口随时间滑动，`dataset.csv` 的字节就会变，后面所有哈希全部作废，链上登记的那一份也就对不上了。固定窗口是整条证据链的第一块地基。

原始 K 线按月分片缓存在 `data/raw`（共 24 个分片 = 12 个月 × 2 个交易对），重跑时优先读缓存，缓存齐全就不发网络请求（`run_record.json` 里 `cache_hit: true`、`network_requests: 0`）。这样既避免"为了更新而重新拉数据"，也避免币安接口抖动影响复现。

清洗规则也尽量少做主观判断：只接受时间戳对齐整点、OHLC 关系合法、窗口内无缺口的数据；重复行只在内容完全一致时才去重，冲突的重复直接报错。宁可失败，也不悄悄补数据。

### 固定模型 revision，并把 checkpoint 折成一个哈希

模型是 `amazon/chronos-t5-small`，但只写模型名不够——同一个名字下面随时可能更新权重。所以我在运行时解析出完整 40 位 revision（本次是 `a971ba21…`），并把整个 snapshot 的文件清单（相对路径、大小、SHA256）按路径排序后做一次 canonical JSON 哈希，得到 `checkpoint_commitment_sha256 = b469a7fd…`。

这就是链上那个 `modelHash` 的来源。它不是单个权重文件的哈希，而是"整个模型目录长什么样"的承诺：换了权重、换了 tokenizer 配置、加了文件，这个值都会变。推理前后各校验一次，中途变了就报错退出。

推理只做一次正式的：CPU、float32、context 64、predict 1，每个点采 20 个样本取中位数，seed 固定。不训练、不微调、不调参。

### τ 只用 validation，先冻结再看 test

τ 的规则提前写死：取 validation 上 `|predicted_return|` 的中位数，`ROUND_HALF_EVEN` 保留 12 位小数，得到 `0.001923089035`。

代码里做了硬性拦截：算 τ 的函数只接受 `split == validation` 的行，传入 test 会直接抛错。这不是形式主义——只要允许"看一眼 test 再回来调阈值"，整个评估就废了，而且这种泄漏很难事后自证。冻结顺序也写进了报告：先出 validation 预测 → 算 τ → 写进 config → 再用这个 τ 生成 test 信号。

信号映射就是三段：大于 τ 买、小于 -τ 卖、其余（包括正好等于边界）持有。边界情况单独做了单元测试，避免浮点比较上的歧义。

### 怎么自证这一步没问题

Step 1 用四个手段互相印证：

1. **产物哈希清单**：8 个确定性文件（dataset、predictions、signals、config、model reference 和三份报告）各自 SHA256，再合成一个 `combined_sha256 = 1eb56cc1…`。
2. **独立校验报告**：行数、时间连续性、窗口边界、τ 来源、信号映射逐项检查，全过才算 `passed`。
3. **离线重放**：把网络 socket 直接封掉，重跑到 `artifacts/replay/step1`，逐字节比对 8 个文件，`passed: true`。
4. **不可覆盖**：`run` 只在空目录里写，重复执行会直接拒绝，避免"调试结果混进正式产物"。

本次产物规模：dataset 17,568 行（8,784 小时 × 2 个交易对），predictions 和 signals 各 8,832 行（validation 4,416 + test 4,416）。信号分布：validation 买 1,077 / 持 2,208 / 卖 1,131；test 买 1,042 / 持 2,220 / 卖 1,154。一次正式推理在 CPU 上约 172 秒。

## Step 2：把结果变成别人能验的对象

### 回测只定四条硬规则

回测不是为了证明策略好，是为了证明"信号能被无歧义地执行"。所以规则写得很少，但每条都检查：

1. 信号在下一根 K 线**开盘**执行、**收盘**结算，用的是信号产生之后才有的价格，不偷看未来。
2. buy 目标仓位 1，sell 目标仓位 0，hold 延续上一期；只做多，不卖空。
3. 只在仓位变化时收 10 bps，成本逐行可复算。
4. validation 和 test 各自从空仓开始，不跨段延续仓位。

时间戳也做了强校验：信号时间必须等于上下文结束时间 + 1 小时，且同一交易对在 split 内逐小时连续。这样"把预测错配到别的时间点"这类错误会直接暴露。

### 为什么是这六件

bundle 固定为 `dataset.csv`、`signals.csv`、`backtest_summary.json`、`config.json`、`model_reference.json`、`manifest.json`。前五件是内容，第六件是清单。

不放原始缓存、不放预测中间文件、不放日志。理由是可验证对象越小越清楚：任何人拿到这六个文件，就知道自己验的是哪一份东西，不需要猜"还有没有第七个文件影响结果"。

### 哈希是怎么串起来的

这是整个 Step 2 最需要讲清楚的地方，顺序不能乱：

1. 每个内容文件按**最终落盘的字节**算 SHA256（不是内存对象，也不是重新序列化一遍）。
2. 五个文件的 `{path, size, sha256}` 按路径升序组成 `files[]`，对它的 canonical JSON 再算一次哈希，得到 `payload_commitment`。
3. `objectId` 由 manifest 的"身份视图"算出——把 `object_id` 字段去掉，其余字段（含 `files[]`、各项哈希、版本号、commit）一起做 canonical JSON 后取 SHA256。
4. 把 `objectId` 写进 manifest，落盘；`manifestHash` 是这最后一版 `manifest.json` 的字节哈希，**存在 bundle 外面**（`manifest_commitment.json` 和链上记录）。

canonical JSON 的规则固定为：UTF-8、键名排序、`(",", ":")` 紧凑分隔、末尾一个换行。CSV 不重新序列化，直接从 Step 1 原样拷贝，并在拷贝后核对哈希。

这里有两个必须绕开的坑：

- **manifest 不能把自己算进 `files[]`**，否则"文件哈希包含 manifest、manifest 又包含文件哈希"永远算不出来。
- **objectId 不能包含自己**，所以要先把 `object_id` 摘掉再算，写回去之后再单独算 `manifestHash`。两轮构造是数学上的必需，不是风格问题。

### 怎么证明它真的防篡改

光说"我算了哈希"没有说服力，得证明"改一点就会被抓"。所以做了三层：

1. **独立校验器**：`tools/verify_bundle.py` 只用标准库，不 import 生成器包，用 `python -I -B` 跑，避免沾到项目环境和缓存。它重算文件哈希、`payload_commitment`、`objectId`、`manifestHash`，并检查是否恰好六个文件、有没有多余目录和符号链接。
2. **11 个负向用例**：改 dataset、改 signals、改 summary、改 config、改 model reference、删文件、改路径、反转顺序、换 `objectId`、加一个额外文件、把 manifest 重新缩进排版——全部必须被拒绝。结果 11/11 拒绝。
3. **换环境复核**：把 bundle 和校验器复制到项目外，用系统自带的 `/usr/bin/python3`（3.9.6，和项目环境的 3.11 不同）再跑一遍，仍然通过；另外把整个 bundle 重新构建到 `artifacts/replay/step2`，六个文件逐字节一致。

三层都过之后才写 `bundle_frozen.json`，把 `frozen` 置为 true。

## Step 3：只把证据写进链上

### 合约故意做小

`SignalRegistry` 只有两个函数：

- `registerSignal(objectId, dataHash, modelHash, signalHash, manifestHash)`：一次性写入，`objectId` 已存在就 revert，成功后发 `SignalRegistered` 事件。
- `getSignal(objectId)`：只读返回六元组，未登记则明确 revert。

没有 owner、没有暂停开关、没有升级代理、没有转账。少一个入口就少一类被质疑的地方：评委不会问"管理员能不能改记录"，因为根本没有管理员。事件里把 `objectId`、四个哈希和时间戳都带上，浏览器里能直接看到，不依赖我方提供的任何截图。

`provider` 取 `msg.sender`，不是传参进来的地址——这样"谁登记的"无法伪造。

### 上链前先把预期值冻住

`preflight` 做的事情是：读 Step 2 冻结的 manifest，重算 `objectId`、四个链上哈希和 `configHash`，连同当前账户地址、chainId 写进 `expected_registration.json`；同时检查余额够不够部署 + 登记。

这一步的意义是"先写答案，再对答案"。等交易确认后再去读链上记录，逐项和这份预期值比，而不是拿链上值反过来解释本地文件。

### 部署、登记、核对

流程是 `preflight → deploy → register → verify-onchain`，每一步都会落一份 JSON。关键的几条：

- 部署前要求 `preflight.status == ready`，且当前账户与预期 `provider` 一致，chainId 必须是 11155111。
- 部署后记录合约地址、部署交易、block number、gas、合约字节码哈希、ABI 哈希。
- 登记时先 `getSignal` 确认该 `objectId` 还没被写过，再发交易；交易回执里解析 `SignalRegistered`，逐字段核对；随后再读一次 `getSignal`，确认链上存储与事件一致。
- 最终 `verify-onchain` 做 18 项只读核对：网络、合约字节码存在、`provider`、四个哈希、时间戳非零、两笔交易回执、事件存在且字段一致、时间戳等于登记区块时间、ABI 哈希、config 哈希、两个 commit、登记报告与最终记录一致。全部通过才生成交接文件。

本次结果：部署交易 `0x4a05fefe…`（block 12132043，gas 2,182,442），登记交易 `0x742eecdd…`（block 12132049，gas 792,291），链上时间戳 `1794646488`。

### 中间真实失败过一次

第一次登记时交易 revert 了，报 `execution reverted`，记录留在 `registration_failure.json`（commit `d238074`）。

原因是脚本原本靠字符串匹配来识别 `SignalNotFound`，但实际拿到的错误里没有可匹配的文字（只有通用的 `execution reverted`），于是脚本把"对象未登记"误判成"未知错误"并中止。修法是按 4 字节 selector 判断（`SignalNotFound(bytes32)` = `0x4b1ef6c9`），改在 `4e7fe699…`。

这里要说清楚：**改的只是脚本读取失败的方式，合约字节码一行没动**（`git log -- contracts/` 只有 `d238074` 一条）。所以部署记录里的 `step3_code_commit = d238074` 和登记记录里的 `registration_code_commit = 4e7fe69` 不一致是正常的，不是"偷偷换了合约"。

这件事也说明保留失败记录的价值：如果当时把失败文件删掉、只留成功记录，反而解释不清为什么登记 commit 和部署 commit 不同。

## 这套东西证明了什么，没证明什么

**能证明**

- 这组文件在某个时刻被某个地址登记过，且链上四个哈希与本地重算完全一致。
- 任何人都能拿这六个文件独立重算，改一个字节就会被发现。
- 生成过程是确定性的：离线重放后六个文件逐字节一致；换 Python 版本、换目录只影响校验侧，不影响已冻结文件的字节。
- τ 没有用 test 选，回测没有偷看未来价格。

**不能证明**

- 数据来源本身真实可信（币安给的原始 K 线是否被篡改，链上管不了）。
- 模型"正确"或"好"（`modelHash` 只说明模型版本没变）。
- 策略赚钱（回测是规则检查，不是业绩）。
- 链上合约的去中心化程度（Sepolia 测试网，单点部署，没有治理）。

答辩时把这条界线主动说清楚，比被人问出来要好。

## 结论与证据对照

| 结论 | 证据 |
| --- | --- |
| 输入是固定的、可复现的 | `artifacts/step1/config.json`、`reports/reproducibility.json`（离线逐字节一致） |
| τ 只来自 validation | `reports/tau_freeze.json` 中 `input_split: validation`、`test_rows_supplied: 0` |
| 回测没有用未来数据 | `artifacts/step2/reports/backtest_checks.json` 七项检查全过 |
| bundle 不可篡改 | `artifacts/step2/reports/negative_checks.json`（11/11 拒绝）、`second_environment.json` |
| bundle 可被别人重建 | `artifacts/step2/reports/reproducibility.json`（六文件字节一致） |
| 链上记录与本地一致 | `artifacts/step3/reports/chain_comparison.json`（18 项全过） |
| 登记确实发生过 | Sepolia 浏览器上的合约地址与两笔交易 |

## 可能被问到的问题

**为什么不用最新数据？**
窗口一变，文件字节就变，所有哈希作废。固定窗口是可验证性的前提，不是偷懒。

**`modelHash` 为什么不是权重文件的哈希？**
单个权重文件哈希不能覆盖 tokenizer 配置、生成参数等。我用的是整个 snapshot 文件清单（路径 + 大小 + 哈希）的汇总承诺，任何一个文件变动都会改变它。

**manifest 为什么不把自己的哈希写进自己？**
自引用悖论，算不出来。所以 `manifestHash` 存在 bundle 外，同时写进链上记录，用链上值反向锚定本地文件。

**10 bps 只在换仓收，会不会太简化？**
会。这是刻意简化：规则少、逐行可复算，才能让人一眼看出成本有没有算错。它不模拟滑点和撮合深度。

**链上到底证明了什么？**
证明了"某个地址在某时刻登记了这组哈希"，以及这组哈希与本地文件一致。它不证明数据真实、模型正确、策略有效。

**如果数据源被换掉呢？**
那 `dataset.csv` 的哈希会变，`objectId` 会变，链上查不到对应记录。哈希保证的是"你手里的这份和登记的那份一致"。

**为什么用测试网？**
课程场景，Sepolia 成本为零且机制与主网一致。合约本身没有主网特化逻辑。

**为什么 `objectId` 和 `manifestHash` 要分开？**
`objectId` 是内容身份，用来做唯一键和防重复登记；`manifestHash` 是文件字节的指纹，用来校验你手里的 manifest 没有被重排或改格式。两者用途不同。

## 数字速查

| 项 | 值 |
| --- | --- |
| 数据窗口 | 2024-01-01 → 2025-01-01 UTC，1h，BTCUSDT/ETHUSDT |
| dataset / predictions / signals 行数 | 17,568 / 8,832 / 8,832 |
| τ | `0.001923089035` |
| 信号分布（validation / test） | 买 1,077 / 1,042，持 2,208 / 2,220，卖 1,131 / 1,154 |
| Step 1 产物组合哈希 | `1eb56cc1ea6f127754c09b065ff3cf489120a75228ce1fe6d68463bf094ecc67` |
| `payload_commitment` | `cf22e40015c603ab2eb904d5bdd85e3604a9972f44523704e4c7108e366ddc6c` |
| `objectId` | `0x1143b163d9a56e29e5714b7cd7ca41c6d05353ceb903704316219060915a4d07` |
| `manifestHash` | `0x3f3fa32d9c98ba929c9e002f0d9e6c35ec46b42a36344c64bdb8426257870ca6` |
| 合约地址 | `0x7BA4b62c97Ce9567075736f7cffAEFafAC8C4E41` |
| 部署 / 登记交易 | `0x4a05fefe…`（gas 2,182,442）/ `0x742eecdd…`（gas 792,291） |
| 区块 | 部署 12,132,043；登记 12,132,049 |
| 链上核对 | 18 项全过 |
| 环境 | Python 3.11.16；第二环境 `/usr/bin/python3` 3.9.6；Node v22.23.2 |

## 如果重做，我会改的地方

1. `artifacts/step1/` 应该一开始就跟 Step 2/3 的产物一起入库，否则别人克隆后没法直接重建 bundle，只能重新跑一遍推理。
2. `EXPECTED_STEP2` 硬编码在脚本里，换版本要手改一整套常量。当时是为了"换个 bundle 就必然报错"的闸门效果，但更稳妥的做法是从 `manifest_commitment.json` 读，再单独存一份"已审核哈希"做二次比对。
3. Step 3 的运行记录只记了 HEAD commit，没记工作树是否干净。Step 1 有 `tracked_tree_clean`，Step 3 应该补上，否则"这个 commit 真能产出这份字节码"少一环。
