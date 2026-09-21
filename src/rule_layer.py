"""规则层：可追溯的硬阈值判断。

与 `amazon-ads-optimization` 的 `ads_opt.mjs diagnose` 同源，用 Python 重写以便
在本流水线里与 Jev 结果逐词对照。

设计原则：**每条结论都必须带依据**。这一层是主路径，Jev 只是影子。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from state_builder import Economics, TermRow

Action = Literal["加价", "降价", "暂停", "保持", "否定"]
LAYER = "rule"


@dataclass
class Verdict:
    term: str
    action: Action
    reason: str          # 可追溯的依据
    priority: str        # P0 / P1 / P2
    layer: str = LAYER

    def as_row(self) -> dict:
        return {
            "关键词": self.term,
            "规则层动作": self.action,
            "规则层依据": self.reason,
            "规则层优先级": self.priority,
        }


def judge(row: TermRow, econ: Economics, target_acos: float) -> Verdict:
    """对单个搜索词出规则层结论。"""
    be = econ.break_even_acos()
    th = row.click_threshold

    # —— P0：无效花费（按 1/CVR 动态阈值，绝对下限 5）——
    if row.orders == 0 and row.clicks >= th > 0:
        return Verdict(
            row.term, "否定",
            f"点击 {int(row.clicks)} ≥ 动态阈值 {th:.0f}（1÷CVR，CVR 取 {row.effective_cvr:.2%}{'，本品无转化时用品类兜底' if row.cvr == 0 else ''}），0 单，花费 {row.spend:.2f}",
            "P0",
        )

    # —— P1：转化速率达标 -> 收割（**必须排在「ACoS 超标就降价」之前**）——
    # 依据 verification.md V17：稳定转化但 ACoS 差的词，恰恰最需要独立活动，
    # 因为「现在是共享出价，你没法单独调它的价」。先降价只是治标。
    orders_per_week = row.orders / max(row.running_days, 1) * 7
    if orders_per_week >= 2:
        return Verdict(
            row.term, "加价",
            f"转化速率 {orders_per_week:.1f} 单/周 ≥ 2，建议独立 Exact 活动并按自身经济性出价"
            f"（该词 ACoS {row.acos:.1%}"
            + (f"，已超盈亏平衡线 {be:.1%}，独立后需先压价" if row.acos > be else "")
            + "）",
            "P1",
        )

    # —— P0：ACoS 越过盈亏平衡线（未达收割速率时）——
    if row.acos >= 0 and row.acos > be and row.clicks >= 20:
        return Verdict(
            row.term, "降价",
            f"ACoS {row.acos:.1%} > 盈亏平衡线 {be:.1%}，点击 {int(row.clicks)}（≥20 最小样本）",
            "P0",
        )

    # —— P1：ACoS 在目标与盈亏线之间 ——
    if row.acos >= 0 and target_acos < row.acos <= be and row.clicks >= 20:
        return Verdict(
            row.term, "降价",
            f"ACoS {row.acos:.1%} 介于目标 {target_acos:.1%} 与盈亏平衡线 {be:.1%} 之间，微调降价",
            "P1",
        )

    # —— P2：效率优秀但样本不足 ——
    if row.clicks < th and row.orders == 0:
        return Verdict(
            row.term, "保持",
            f"点击 {int(row.clicks)} < 动态阈值 {th:.0f}，样本不足，继续观察",
            "P2",
        )

    # —— P1：效率优秀 ——
    if row.acos >= 0 and row.acos <= target_acos * 0.7:
        return Verdict(
            row.term, "加价",
            f"ACoS {row.acos:.1%} ≤ 目标×0.7，效率优秀，可提价争取曝光",
            "P1",
        )

    acos_txt = f"{row.acos:.1%}" if row.acos >= 0 else "无成交"
    return Verdict(row.term, "保持", f"未触发任何阈值（ACoS {acos_txt} / 点击 {int(row.clicks)} / 阈值 {th:.0f}）", "P2")


def judge_all(rows: list[TermRow], econ: Economics, target_acos: float) -> list[Verdict]:
    return [judge(r, econ, target_acos) for r in rows]

