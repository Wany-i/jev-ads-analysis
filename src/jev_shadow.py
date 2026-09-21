"""Jev 影子层：调 `ad_keyword_action` 做语义判断，或在不具备条件时干跑。

**定位：影子层，不是主路径。**
理由：规则层的每条结论都可追溯（这是本项目的核心资产），而 Jev 输出的是黑盒概率。
把 Jev 放在主路径会摧毁可追溯性。影子的价值在于**分歧检测**。

三条硬约束（来自 jev-decision-layer 的实测）：
1. 算术全部在 state_builder 完成，这里只传结果
2. state 只给最小字段集，避免 context rot
3. 置信度阈值必须在「实际会发的 state 形状」上标定后才能启用自动执行

因此在未标定前，本模块默认 `dry_run=True`，输出「未接入」而不是猜测。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

# —— 端点与模型（见 jev-decision-layer/README）——
# 注意：该模型不能走 chat/completions；decisions 端点在 alpha 路径下，无 /v1
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
API_KEY_ENV = "OPENROUTER_API_KEY"

# —— 分动作门控阈值（来自 jev-decision-layer/registry/ad_keyword_action.json）——
# 低风险动作阈值低，高风险动作阈值高：keep 错了只是少赚，pause/negate 错了直接丢流量
GATE_THRESHOLDS = {
    "加价": 0.85,   # raise
    "降价": 0.70,   # lower
    "暂停": 0.90,   # pause
    "保持": 0.50,   # keep
    "否定": 0.75,   # negate
}
DEFAULT_THRESHOLD = 0.70
# 无效花费概率超过此值时，无论置信度多高都转人工（不可轻易回滚的动作）
WASFUL_REVIEW_GUARD = 0.80

ACTION_EN2CN = {
    "raise": "加价", "lower": "降价", "pause": "暂停",
    "keep": "保持", "negate": "否定", "other": "保持",
}

# 判断口径必须写在 instructions 里，不能只写在 state（或干脆不写）。
# 依据：jev-decision-layer 实测 —— 只删掉 instructions 里的口径，同一判断的
# noul 从 0.59 掉到 0.33。我们第一版只写了「按目标 ACOS 口径」，没说口径是什么，
# 结果 12 个词里 10 个被判「暂停」（连 ACoS 26% 的健康词也是），分歧率 91.7%。
# 下面把规则层的判据显式写进口径，但把「怎么权衡」留给模型。
INSTRUCTIONS_ACTION = (
    "判断这个词下一步最该做的动作。判断口径（按优先级依次比较）："
    "① 点击已达「动态点击阈值」且订单为 0 —— 属无效花费，选 negate 或 pause；"
    "② 实测ACOS 高于「盈亏平衡ACOS」且点击 ≥ 20 —— 在亏钱，选 lower；严重偏高选 pause；"
    "③ 实测ACOS 介于「目标ACOS」与「盈亏平衡ACOS」之间 —— 效率可用但未达标，选 lower；"
    "④ 实测ACOS 低于「目标ACOS」的 0.7 倍，且曝光受限 —— 效率优秀，选 raise；"
    "⑤ 点击少于「动态点击阈值」且订单为 0 —— 样本不足，选 keep，不要否定也不要暂停；"
    "⑥ 判断依据不足或以上都不贴合时，选 other。"
)

QUESTIONS: dict[str, Any] = {
    "动作": {
        "type": "choice",
        "instructions": INSTRUCTIONS_ACTION,
        "criteria": {
            "raise": "提高出价以抢更多流量",
            "lower": "降低出价",
            "pause": "暂停投放该词",
            "keep": "保持现状继续观察",
            "negate": "加为否定词",
            "other": "以上都不合适",
        },
    },
    "无效花费": {
        "type": "noul",
        "instructions": "该词到目前的花费已经属于明显的无效支出",
    },
    "紧迫度": {
        "type": "score",
        "instructions": "处理这个判断的紧迫程度",
        "criteria": ["放着不管", "本周内处理", "今天就该处理"],
    },
}


@dataclass
class JevVerdict:
    term: str
    action: str          # 中文动作；未接入时为「未接入」
    confidence: float
    urgency: float | None
    waste_prob: float | None
    gate: str            # auto / review / dry_run
    note: str = ""

    def as_row(self) -> dict:
        return {
            "关键词": self.term,
            "Jev动作": self.action,
            "Jev置信度": round(self.confidence, 4) if self.confidence else None,
            "Jev紧迫度": self.urgency,
            "无效花费概率": round(self.waste_prob, 4) if self.waste_prob is not None else None,
            "Jev门控": self.gate,
        }


def _gate(action: str, confidence: float, waste_prob: float | None) -> str:
    """置信度门控 + 硬约束。见 jev-decision-layer/docs 第 7 节。"""
    if waste_prob is not None and waste_prob > WASFUL_REVIEW_GUARD and action in ("否定", "暂停"):
        return "review"
    return "auto" if confidence >= GATE_THRESHOLDS.get(action, DEFAULT_THRESHOLD) else "review"


def _call(state: dict, *, timeout: int = 120) -> dict:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(f"未配置 {API_KEY_ENV}；可先用 dry_run=True 干跑")
    body = json.dumps({"model": MODEL, "state": state, "questions": QUESTIONS}).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # 网关偶发 5xx（实测遇到 Cloudflare 520）不值得直接失败：
    # 对 5xx / 429 / 网络超时做有限退避重试；4xx（除 429）是请求本身的问题，不重试。
    import time as _time
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code >= 500 or e.code == 429:
                last = f"HTTP {e.code}: {detail}"
                if attempt < 2:
                    _time.sleep(2 * (attempt + 1))
                    continue
            raise RuntimeError(f"Jev 调用失败 HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
            if attempt < 2:
                _time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"Jev 调用失败（网络）: {e}") from e
    raise RuntimeError(f"Jev 调用失败，重试 3 次仍不成功: {last}")


def judge(term: str, state: dict, *, dry_run: bool = True) -> JevVerdict:
    """对单个词做 Jev 判断。dry_run 时不联网，返回「未接入」。"""
    if dry_run:
        return JevVerdict(
            term, "未接入", 0.0, None, None, "dry_run",
            "未标定：需先用 20~50 个真实关键词做影子测试再启用",
        )

    resp = _call(state)
    answers = resp.get("answers", {}) or {}
    act = answers.get("动作", {}) or {}
    cn = ACTION_EN2CN.get(str(act.get("choice", "")).lower(), "保持")
    conf = float(act.get("confidence", 0.0) or 0.0)

    waste = answers.get("无效花费", {}) or {}
    # Noul 的返回值就在 "noul" 字段里（实测原始响应：{"type":"noul","noul":0.88}），
    # 不是 probability —— 之前读错字段名，导致这个值一直是 None。
    waste_p = None
    for k in ("noul", "probability", "value"):
        if waste.get(k) is not None:
            try:
                waste_p = float(waste[k]); break
            except (TypeError, ValueError):
                pass

    urg = answers.get("紧迫度", {}) or {}
    # Score 的返回值字段名在不同版本间可能是 score / level / value，
    # 逐个尝试，避免因字段名不匹配而静默拿到 None（第一版就是这个 bug）。
    urg_v = None
    for k in ("score", "level", "value", "rating"):
        if urg.get(k) is not None:
            try:
                urg_v = float(urg[k]); break
            except (TypeError, ValueError):
                pass

    return JevVerdict(term, cn, conf, urg_v, waste_p, _gate(cn, conf, waste_p))


def judge_all(rows: list[dict], *, dry_run: bool = True) -> list[JevVerdict]:
    """rows: [{"term": ..., "state": {...}}, ...]"""
    return [judge(r["term"], r["state"], dry_run=dry_run) for r in rows]
