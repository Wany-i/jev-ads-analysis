"""把广告后台导出裁剪成 Jev 能吃的 state。

设计依据（来自 jev-decision-layer 的实测结论）：
- Jev 不做算术、且不可靠计数 -> 所有比例/差值/天数必须在这里算完
- Jev 有 context rot：无关内容会扭曲置信度（实测 ±0.33）
  -> 只给判断需要的字段，绝不整表塞进去
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 列名别名表：兼容不同站点/工具的导出表头
COLUMN_ALIASES = {
    "term": ["customer search term", "search term", "搜索词", "keywords", "keyword"],
    "campaign": ["campaign name", "campaign", "广告活动"],
    "match_type": ["match type", "匹配方式", "targeting type"],
    "impressions": ["impressions", "曝光", "曝光量"],
    "clicks": ["clicks", "点击", "点击量"],
    "spend": ["spend", "cost", "花费", "广告花费"],
    "sales": ["7 day total sales", "14 day total sales", "sales", "销售额", "广告销售额"],
    "orders": ["7 day total orders (#)", "14 day total orders (#)", "orders", "订单", "订单数"],
}

MATCH_TYPE_CN = {
    "auto": "自动", "automatic": "自动",
    "broad": "广泛", "phrase": "词组", "exact": "精准",
    "product targeting": "商品投放", "asin": "商品投放",
    # 真实报表里，自动广告的 Match Type 常为空或 "-"（没有匹配方式的概念）
    "-": "自动", "": "自动", "close match": "自动", "loose match": "自动",
    "substitutes": "自动", "complements": "自动",
}
# 归一后的合法取值（与多维表格 select 选项对齐）
VALID_MATCH_TYPES = {"自动", "广泛", "词组", "精准", "商品投放"}


def _norm(s: str) -> str:
    return str(s or "").strip().lower().replace("_", " ").replace("-", " ")


def _build_column_map(headers: list[str]) -> dict[str, int]:
    m: dict[str, int] = {}
    normed = [_norm(h) for h in headers]
    for key, alts in COLUMN_ALIASES.items():
        for alt in alts:
            na = _norm(alt)
            if na in normed:
                m[key] = normed.index(na)
                break
        if key not in m:
            for i, h in enumerate(normed):
                for alt in alts:
                    na = _norm(alt)
                    if len(na) >= 3 and na in h:
                        m[key] = i
                        break
                if key in m:
                    break
    return m


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", "").replace("$", "").replace("%", "").replace("¥", "")
    if s in ("", "-", "--"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


@dataclass
class Economics:
    """单位经济学参数。盈亏平衡 ACoS 的唯一来源。"""

    price: float
    cost: float = 0.0
    fba: float = 0.0
    first_leg: float = 0.0
    commission_rate: float = 0.15   # 平台佣金率
    return_rate: float = 0.0        # 退货率
    return_loss_rate: float = 1.0   # 退货损失率（1.0 = 整单损失）

    def contribution(self) -> float:
        return (
            self.price
            - self.cost
            - self.fba
            - self.first_leg
            - self.price * self.commission_rate
            - self.price * self.return_rate * self.return_loss_rate
        )

    def break_even_acos(self) -> float:
        if self.price <= 0:
            return 0.0
        return self.contribution() / self.price

    def affordable_cpc(self, cvr: float, target_acos: float) -> float:
        """可接受 CPC = 售价 x 转化率 x 目标 ACoS"""
        return self.price * cvr * target_acos


@dataclass
class TermRow:
    """一个搜索词的完整状态。所有派生量在这里算好，Jev 只读不算。"""

    term: str
    campaign: str
    match_type: str
    impressions: float
    clicks: float
    spend: float
    sales: float
    orders: float
    running_days: int
    current_bid: float
    # —— 以下均为代码计算 ——
    acos: float = field(init=False)
    cvr: float = field(init=False)
    ctr: float = field(init=False)
    cpc: float = field(init=False)
    click_threshold: float = field(init=False)   # 1 / CVR，下限 5
    effective_cvr: float = field(init=False)     # 实际用于推导阈值的 CVR
    # 品类兜底 CVR：单词自身无转化时用它来推 1/CVR。
    # 依据：独立实现要求 "Fall back to your catalog median conversion rate
    # if a product doesn't have its own."（见 amazon-ads-optimization 的 verification.md V2）
    # 注意：它只能在读完整张表后才知道（见 load_rows），因此构造时通常为 0，
    # 设置之后必须调用 refresh() 重算，否则 click_threshold 会停在 0（等于永不判断）。
    fallback_cvr: float = 0.0

    def __post_init__(self) -> None:
        self.acos = (self.spend / self.sales) if self.sales > 0 else -1.0
        self.cvr = (self.orders / self.clicks) if self.clicks > 0 else 0.0
        self.ctr = (self.clicks / self.impressions) if self.impressions > 0 else 0.0
        self.cpc = (self.spend / self.clicks) if self.clicks > 0 else 0.0
        self.refresh()

    def refresh(self) -> None:
        """重算依赖 fallback_cvr 的派生量。

        fallback_cvr 是品类级参数，只有读完整张表才知道，构造时拿不到。
        如果设置后不重算，click_threshold 会停在 0 —— 等于「永远不判断」，
        这是曾经踩过的 bug（零单词全部被误判为「保持」）。
        """
        eff_cvr = self.cvr if self.cvr > 0 else self.fallback_cvr
        self.click_threshold = max(5.0, round(1.0 / eff_cvr)) if eff_cvr > 0 else 0.0
        self.effective_cvr = eff_cvr


    def match_type_cn(self) -> str | None:
        """归一匹配方式；无法归一时返回 None（由调用方决定是否跳过该字段）。"""
        v = MATCH_TYPE_CN.get((self.match_type or "").strip().lower())
        return v if v in VALID_MATCH_TYPES else None

    def to_jev_state(self, econ: Economics, target_acos: float) -> dict[str, Any]:
        """产出 Jev state —— 只含判断需要的最小字段集。"""
        return {
            "站点": "US",
            "目标ACOS": round(target_acos, 4),
            "盈亏平衡ACOS": round(econ.break_even_acos(), 4),
            "本次判断对象": {
                "关键词": self.term,
                "匹配方式": self.match_type_cn() or "未知",
                "曝光": int(self.impressions),
                "点击": int(self.clicks),
                "花费": round(self.spend, 2),
                "销售额": round(self.sales, 2),
                "订单": int(self.orders),
                "运行天数": int(self.running_days),
                "当前出价": round(self.current_bid, 2),
                # 代码算好的派生量：Jev 不做算术
                "实测ACOS": round(self.acos, 4) if self.acos >= 0 else "无成交",
                "CVR": round(self.cvr, 4),
                "CPC": round(self.cpc, 4),
                "动态点击阈值": self.click_threshold,
            },
        }


def load_rows(path: str | Path, running_days: int = 30, current_bid: float = 0.0) -> list[TermRow]:
    """读 CSV，返回 TermRow 列表。

    running_days / current_bid 若不在报表里，由调用方提供。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delim = "\t" if first.count("\t") > first.count(",") else ","
    reader = csv.reader(text.splitlines(), delimiter=delim)
    try:
        headers = next(reader)
    except StopIteration as e:
        raise ValueError("空文件") from e
    cmap = _build_column_map(headers)
    required = ["term", "clicks", "orders", "spend"]
    missing = [k for k in required if k not in cmap]
    if missing:
        raise ValueError(f"缺少必需列: {missing}；现有列: {headers}")

    rows: list[TermRow] = []
    for r in reader:
        if not any(str(c).strip() for c in r):
            continue
        get = lambda k: r[cmap[k]] if k in cmap and cmap[k] < len(r) else ""  # noqa: E731
        rows.append(
            TermRow(
                term=str(get("term")).strip(),
                campaign=str(get("campaign")).strip(),
                match_type=str(get("match_type")).strip(),
                impressions=_num(get("impressions")),
                clicks=_num(get("clicks")),
                spend=_num(get("spend")),
                sales=_num(get("sales")),
                orders=_num(get("orders")),
                running_days=running_days,
                current_bid=_num(get("current_bid")) or current_bid,
            )
        )
    rows = [r for r in rows if r.term]
    # 品类兜底 CVR = 全量订单 ÷ 全量点击（数据自身的中位水平）
    tot_clicks = sum(r.clicks for r in rows)
    tot_orders = sum(r.orders for r in rows)
    catalog_cvr = (tot_orders / tot_clicks) if tot_clicks > 0 else 0.0
    for r in rows:
        r.fallback_cvr = catalog_cvr
        r.refresh()
    return rows


