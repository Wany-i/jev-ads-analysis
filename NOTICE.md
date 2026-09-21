# 来源与授权说明（NOTICE）

## 一、上游项目

| 项目 | 授权 | 本仓库如何引用 |
|---|---|---|
| [`Wany-i/amazon-ads-optimization`](https://github.com/Wany-i/amazon-ads-optimization) | MIT | 引用其**阈值与依据**（规则层的判断逻辑按 Python 重写，未整段复制其 JS 脚本） |
| [`Wany-i/jev-decision-layer`](https://github.com/Wany-i/jev-decision-layer) | MIT | 引用其**结论与接口约定**（动作空间、分动作阈值、三条硬约束、边界调研结论） |

两个上游项目同属 `Wany-i`，授权一致。

## 二、第三方内容

本仓库**不含**任何直接复制的第三方正文。文档中提及的以下内容均为**结论引用**：

- **TypeSafe AI / Jev** — 官方文档 `docs.typesafe.ai`。本仓库引用的官方结论包括：
  「Jev is TypeSafe's flagship model and the first System One model」、
  Choice/Score/Noul 三种 primitive、confidence-gated routing 模式、
  use-case-map 中的「广告」场景范围。
  官方文档内容版权归 TypeSafe AI 所有，如需引用请访问官方站点。
- **Amazon** — 广告相关术语与指标口径属 Amazon 定义，本仓库不复制其文档。

> 本仓库**不含** `jev-decision-layer` 中由 AI 调研整理的大体量调研文档（约 300KB），
> 只引用其结论。原始调研与其引用出处见上游仓库 `docs/`。

## 三、无凭据声明

- 不含任何 API Key / token / Cookie
- `OPENROUTER_API_KEY` 仅运行时从环境变量读取，不写盘、不进日志
- 飞书 Base 的 token 由使用者通过命令行传入，不落盘到仓库

## 四、权利主张

- 本仓库原创代码与文档：MIT（见 [LICENSE](LICENSE)）
- 被引用内容的版权归各自权利人
- 若权利人认为引用超出合理范围，请提 Issue，我们会立即调整或移除

## 五、免责

本项目与 **Amazon.com, Inc.**、**TypeSafe AI** 均无隶属或背书关系。
