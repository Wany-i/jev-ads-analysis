"""规则层 vs Jev 影子层的比对 —— 分歧检测。

**这是整个集成里最有价值的部分。**

规则层在边界上是硬的（ACoS 恰好是目标 1.1 倍时，规则会机械地说「微调降价」）。
Jev 提供独立第二意见。两者不一致 = 这个案例确实模糊，正是最该让人看的地方。

注意：分歧检测**不依赖置信度是否校准精确** —— 它只用「选中的答案」。
而 jev-decision-layer 的实测显示：**选中的答案极稳**（state 放大 41 倍，答案 10/10 不变），
不稳的是置信度。所以这个用法恰好用在了 Jev 最可靠的那一面。
"""

from __future__ import annotations

from dataclasses import dataclass

from jev_shadow import JevVerdict
from rule_layer import Verdict


@dataclass
class Comparison:
    term: str
    rule_action: str
    rule_reason: str
    rule_priority: str
    jev_action: str
    jev_confidence: float
    jev_gate: str
    # 这两个字段解析于 jev_shadow.JevVerdict，但此前没有透传到 Comparison，
    # 导致最终输出里永远是 None（实跑发现的 bug：解析对了但数据在中间环节丢了）
    jev_urgency: float | None
    jev_waste_prob: float | None
    diverged: bool
    disposition: str          # 自动执行 / 人工复核 / 待标定
    note: str

    def as_row(self) -> dict:
        return {
            "关键词": self.term,
            "规则层动作": self.rule_action,
            "规则层依据": self.rule_reason,
            "规则层优先级": self.rule_priority,
            "Jev动作": self.jev_action,
            "Jev置信度": round(self.jev_confidence, 4) if self.jev_confidence else None,
            "Jev门控": self.jev_gate,
            "Jev紧迫度": self.jev_urgency,
            "无效花费概率": self.jev_waste_prob,
            "是否分歧": self.diverged,
            "处置": self.disposition,
            "备注": self.note,
        }


# 语义等价映射：两个动作若可互相替代，不算分歧
# 例如规则说「降价」而 Jev 说「否定」，对高花费零单词来说方向一致，但强度不同 —— 记为「强度分歧」
EQUIVALENT = {
    ("降价", "否定"): "强度分歧",   # 都指向收，但力度差一档
    ("加价", "保持"): "强度分歧",   # 都指向不动或略放
    ("保持", "降价"): "强度分歧",
}
HIGH_RISK = {"暂停", "否定"}


def compare(rule: Verdict, jev: JevVerdict) -> Comparison:
    diverged = False
    note = ""
    disposition = "自动执行"

    if jev.action == "未接入":
        # Jev 未接入：只出规则层结论，一律标记待标定。
        # 必须在这里直接返回 —— 否则会落到下面的高风险分支，
        # 把"待标定"错误覆盖成"人工复核"（实跑发现的 bug）。
        return Comparison(
            term=rule.term,
            rule_action=rule.action,
            rule_reason=rule.reason,
            rule_priority=rule.priority,
            jev_action=jev.action,
            jev_confidence=jev.confidence,
            jev_gate=jev.gate,
            jev_urgency=jev.urgency,
            jev_waste_prob=jev.waste_prob,
            diverged=False,
            disposition="待标定",
            note=jev.note or "Jev 层未接入，仅使用规则层结论",
        )
    if jev.action == rule.action:
        note = f"两层一致（Jev 置信度 {jev.confidence:.2f}）"
    else:
        key = (rule.action, jev.action)
        if key in EQUIVALENT:
            diverged = True
            note = f"强度分歧：规则={rule.action}，Jev={jev.action}（{EQUIVALENT[key]}）"
        else:
            diverged = True
            note = f"方向分歧：规则={rule.action}，Jev={jev.action}"

    # 处置规则
    if diverged:
        disposition = "人工复核"
    elif rule.action in HIGH_RISK or jev.action in HIGH_RISK:
        # 高风险动作：即使两层一致，也要求 Jev 门控为 auto 才自动执行
        disposition = "自动执行" if jev.gate == "auto" else "人工复核"
    elif rule.priority == "P0" and jev.gate == "review":
        disposition = "人工复核"
        note = (note + "；" if note else "") + "规则判为 P0 但 Jev 门控为 review"

    return Comparison(
        term=rule.term,
        rule_action=rule.action,
        rule_reason=rule.reason,
        rule_priority=rule.priority,
        jev_action=jev.action,
        jev_confidence=jev.confidence,
        jev_gate=jev.gate,
        jev_urgency=jev.urgency,
        jev_waste_prob=jev.waste_prob,
        diverged=diverged,
        disposition=disposition,
        note=note,
    )


def summarize(rows: list[Comparison]) -> dict:
    total = len(rows)
    scored = [r for r in rows if r.jev_action != "未接入"]
    return {
        "总词数": total,
        "已接入 Jev 词数": len(scored),
        "分歧数": sum(1 for r in rows if r.diverged),
        "分歧率": round(sum(1 for r in rows if r.diverged) / total, 4) if total else None,
        "自动执行": sum(1 for r in rows if r.disposition == "自动执行"),
        "人工复核": sum(1 for r in rows if r.disposition == "人工复核"),
        "待标定": sum(1 for r in rows if r.disposition == "待标定"),
    }

