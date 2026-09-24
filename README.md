# jev-ads-analysis · Jev 驱动的广告优化分析（参考实现）

> 把**决策模型 Jev** 接进**亚马逊广告优化**的参考实现：
> 规则层出可追溯结论，Jev 出独立第二意见，**分歧即信号**。
> 端到端跑通，输出可直接落到**飞书多维表格**供人复核。

[![CI](https://github.com/Wany-i/jev-ads-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/Wany-i/jev-ads-analysis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

---

## ⚠️ 先读这一段：本项目的定位

**这是一个参考实现，不是生产工具。**

它要回答的是「**Jev 这类决策模型，能不能参与广告优化决策，怎么参与才不出事**」——
而不是交付一个可以闭眼自动调价的机器人。

**标定状态**：已在 **45 个真实搜索词**上完成首次阈值标定（见 [docs/04-阈值标定.md](docs/04-阈值标定.md)）。
结论：**confidence 有区分度**（一致组均值 0.820 vs 分歧组 0.555），一致率曲线在 0.80 出现拐点。

**Jev 层默认关闭。** 不配置 `OPENROUTER_API_KEY`、不跑标定，它只会输出「未接入 / 待标定」。
这不是偷懒，是设计：见下方「为什么默认关闭」。

---

## 一、为什么要做影子层，而不是让 Jev 直接决策

| | 规则层（主路径） | Jev（影子层） |
|---|---|---|
| 输出 | 动作 + **可追溯依据** | 动作 + 概率 + 置信度 |
| 能否回答「为什么」 | ✅「ACoS 120% > 盈亏平衡线 33.7%，点击 40」 | ❌ 只能说「模型给了 0.83」 |
| 稳定性 | 确定（同输入同输出） | 答案稳、**置信度不稳** |

**核心冲突**：广告优化最需要的可解释性，恰好是决策模型给不了的。
把 Jev 放主路径，等于用黑盒概率替代可追溯依据 —— 出问题时无法复盘。

**所以正确用法是影子层**，而影子的价值不在「同意」，在「**不同意**」：

> 规则层在边界上是硬的。ACoS 恰好是目标 1.1 倍时，规则会机械地说「微调降价 10%」。
> Jev 给出独立第二意见。**两者不一致 = 这个案例确实模糊，正是最该让人看的地方。**

### 为什么分歧检测是可靠的（而置信度不是）

`jev-decision-layer` 做了 40 次对照实验：把 state 从 630 token 加到 26,200 token（41 倍），

- **选中的答案 10/10 一次没变** ✅
- **置信度漂移 ±0.33** ❌（清晰案子虚高 +0.09，模糊案子崩塌 −0.33）

**分歧检测只用「选中的答案」，不用置信度的绝对值** —— 恰好用在了 Jev 最可靠的那一面。
这也是本项目敢用它的原因。

---

## 二、为什么默认关闭 Jev

`jev-decision-layer` 的实测结论给出了硬前提：

> **阈值必须在「你实际会发的 state 形状」上调，换了 state 结构就要重标。**
> **上线前先做影子测试（20–50 个真实关键词），这是进 skill 的前置条件。**

置信度漂 ±0.33 意味着：拍脑袋定阈值，会在**两个方向上同时出错**——

- 清晰案子置信度虚高 → **本该复核的被自动执行**
- 模糊案子置信度崩塌 → **本该执行的被推进人工队列**

而**这两种错误你都看不见**，因为置信度本身就是你以为用来判断「该不该信」的那个数。

所以本项目的默认行为是：**跑通全链路，但把 Jev 结果留空，等标定。**

---

## 三、快速开始

```bash
# 只需要 Python 3.10+，无第三方依赖
python src/pipeline.py \
  --input examples/input-sample.csv \
  --price 39.99 --cost 8 --fba 7.5 --first-leg 3 \
  --commission-rate 15 --return-rate 5 \
  --target-acos 25 --running-days 30 --bid 0.85 \
  --out output/pipeline-output.json
```

输出（节选，实测）：

```
portable blender          点击 18 | 阈值 33 | 保持 | 点击 18 < 动态阈值 33，样本不足，继续观察
blender bottle            点击 40 | 阈值 40 | 降价 | ACoS 120.0% > 盈亏平衡线 33.7%，点击 40（≥20 最小样本）
smoothie maker            点击 32 | 阈值 11 | 降价 | ACoS 32.0% 介于目标 25.0% 与盈亏平衡线 33.7% 之间
juicer machine            点击 60 | 阈值 30 | 降价 | ACoS 90.0% > 盈亏平衡线 33.7%，点击 60
```

### 分析载体：飞书多维表格（已跑通实例）

```bash
python src/bitable_sync.py \
  --base-token <base_token> --table-id <table_id> \
  --input output/pipeline-output.json --dry-run
```

去掉 `--dry-run` 即写入（需要 `lark-cli` 已登录且具备 `base:record:create` 等权限）。

**已验证实例**：<https://cathypromotion.feishu.cn/base/V2XMb01AfahmKYsDDahcIXiZn3f>
（24 字段 / 7 条示例记录 / 3 个筛选视图：待复核 · 分歧案例 · P0止血）

**为什么选多维表格做分析载体**：

- 分歧案例**需要人看**，表格天然适合筛选/分组/批注
- 一层「是否分歧」筛选视图就能把需要复核的案例挑出来
- **规则层依据与 Jev 输出并排**，人能直接对比两层的判断，而不是只看一个结论

---

## 四、流水线

```
广告导出 CSV
   ↓
[state_builder]  算好所有派生量，裁剪成 Jev 能吃的 state
   |  盈亏平衡ACOS / 实测ACOS / CVR / CPC / 动态点击阈值
   ↓
[rule_layer]     规则层 -> 动作 + 可追溯依据        ← 主路径
   ↓
[jev_shadow]     Jev -> 动作 + 置信度 + 紧迫度      ← 影子（默认 dry-run）
   ↓
[compare]        比对 -> 一致 / 强度分歧 / 方向分歧
   ↓
[bitable_sync]   落到飞书多维表格，供人复核
```

---

## 五、Jev 的四条硬边界（已在代码里绕开）

来自官方文档与 `jev-decision-layer` 的实测：

| # | 边界 | 本项目怎么绕 |
|---|---|---|
| 1 | **不是计算器，且不可靠计数** | `state_builder.py` 里算完 ACoS / CVR / CPC / 阈值，**只把结果传给 Jev** |
| 2 | **上下文腐化**：无关内容扭曲置信度（±0.33） | Jev 的 state 只含判断需要的字段，**绝不整表塞进去** |
| 3 | **不用日期比较**（把日期当文本） | `运行天数` 由代码算好传入 |
| 4 | **不生成文本** | 只用于动作判断，不用于写文案 |

**官方还提醒**：官方 use-case-map 的「广告」场景指的是**评估创意素材、落地页、投放位置上下文**，
**不含「加价/降价/暂停/否词」这类竞价动作** —— 本项目做的是官方未列的自定义场景，所以更要自己验。

---

## 六、仓库结构

```
jev-ads-analysis/
├── src/
│   ├── state_builder.py   # 广告导出 -> Jev state（算好全部算术）
│   ├── rule_layer.py      # 规则层，每条结论带依据
│   ├── jev_shadow.py      # Jev 影子层（分动作门控 + 硬约束）
│   ├── compare.py         # 分歧检测
│   ├── bitable_sync.py    # 同步到飞书多维表格
│   └── pipeline.py        # 编排入口
├── docs/
│   ├── 01-定位与边界.md
│   ├── 02-分析载体-飞书多维表格.md
│   ├── 03-与上游仓库的关系.md
│   ├── 04-阈值标定.md            # 45 词影子对照与阈值曲线
│   └── 05-字段说明.md            # 置信度 / 紧迫度 / 无效花费概率 的语义
├── examples/input-sample.csv
└── LICENSE · CONTRIBUTING.md · CHANGELOG.md · SECURITY.md · NOTICE.md
```

---

## 七、常见问题

**Q：没有 `OPENROUTER_API_KEY` 能跑吗？**
能。默认 `--jev dry-run`，全链路跑通，Jev 列输出「未接入」，处置列是「待标定」。

**Q：能直接用它自动调广告吗？**
**不能，也不该。** 本项目不做任何写操作，输出是给人看的分析结果。

**Q：规则层的阈值从哪来？**
来自 [`amazon-ads-optimization`](https://github.com/Wany-i/amazon-ads-optimization) 的
`references/verification.md` —— 六轮交叉验证（11 个独立实现 + 32 个中文来源 + 2 个官方来源）。
其中动态点击阈值 `1 ÷ CVR` 有中文独立复现。

**Q：Jev 的置信度阈值我该定多少？**
**不要照抄。** 跑影子测试，用你自己的数据扫多个阈值，看准确率/覆盖率曲线再定。

---

## 八、上游项目

| 项目 | 本项目的用法 |
|---|---|
| [`Wany-i/jev-decision-layer`](https://github.com/Wany-i/jev-decision-layer) | Jev 的调用层、`ad_keyword_action` 决策定义、门控与硬约束、能力边界调研 |
| [`Wany-i/amazon-ads-optimization`](https://github.com/Wany-i/amazon-ads-optimization) | 规则层阈值与依据（盈亏平衡 ACoS、`1÷CVR`、广告位倍数等） |

详见 [docs/03-与上游仓库的关系.md](docs/03-与上游仓库的关系.md)。

## 许可

原创内容 [MIT](LICENSE)；第三方来源见 [NOTICE.md](NOTICE.md)。

> 本项目与 Amazon.com, Inc.、TypeSafe AI 均无隶属或背书关系。




## 项目全流程文档

集成、发布与交付视角的文档在 [`docs/项目全流程/`](docs/项目全流程/)；阅读顺序与约定见 [00-阅读指引](docs/项目全流程/00-阅读指引.md)。正文四篇如下：

- [06-实现说明](docs/项目全流程/06-实现说明.md)：代码结构、数据流、核心模块和实现时容易误判的细节。
- [07-集成与载体](docs/项目全流程/07-集成与载体.md)：Jev 影子层如何接入、分析结果如何落到飞书多维表格，以及取数与依赖边界。
- [08-发布与交付](docs/项目全流程/08-发布与交付.md)：版本策略、发布流程、校验和与验收清单。
- [09-踩坑与决策记录](docs/项目全流程/09-踩坑与决策记录.md)：实跑缺陷、设计推翻、保留决策和防复发措施。

方法论与验证部分见 [Wany-i/amazon-ads-optimization](https://github.com/Wany-i/amazon-ads-optimization) 仓库。
