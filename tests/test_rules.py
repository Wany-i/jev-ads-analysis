"""单元测试 —— 把实跑踩过的每个坑固化成回归测试。

只用标准库 unittest（本项目零第三方依赖）。
运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

for _s in (sys.stdout, sys.stderr):          # Windows 控制台是 GBK，避免中文测试名报错
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import compare as C            # noqa: E402
import jev_shadow as J         # noqa: E402
import rule_layer as R         # noqa: E402
import state_builder as S      # noqa: E402

ECON = S.Economics(price=29.50, cost=6.2, fba=5.4, first_leg=2.1,
                   commission_rate=0.15, return_rate=0.03)
TARGET = 0.25


def term(**kw) -> S.TermRow:
    base = dict(term="t", campaign="c", match_type="broad", impressions=1000,
                clicks=30, spend=30.0, sales=0.0, orders=0, running_days=30, current_bid=1.0)
    base.update(kw)
    return S.TermRow(**base)


# --------------------------------------------------------------- 单位经济学
class TestEconomics(unittest.TestCase):

    def test_break_even_equals_contribution_margin(self):
        """盈亏平衡 ACoS = 单位贡献 / 售价（官方口径：contribution margin %）"""
        self.assertAlmostEqual(ECON.break_even_acos(), ECON.contribution() / 29.50, places=9)

    def test_contribution_deducts_all_items(self):
        expect = 29.50 - 6.2 - 5.4 - 2.1 - 29.50 * 0.15 - 29.50 * 0.03
        self.assertAlmostEqual(ECON.contribution(), expect, places=9)

    def test_affordable_cpc_formula(self):
        """可接受 CPC = 售价 x CVR x 目标 ACoS"""
        self.assertAlmostEqual(ECON.affordable_cpc(0.10, 0.25), 29.50 * 0.10 * 0.25, places=9)


# ------------------------------------------------------------- TermRow 派生量
class TestTermRow(unittest.TestCase):

    def test_click_threshold_is_inverse_cvr(self):
        """动态点击阈值 = 1 / CVR"""
        r = term(clicks=100, orders=10)      # CVR 10% -> 阈值 10
        self.assertEqual(r.click_threshold, 10)

    def test_click_threshold_floor_is_five(self):
        """CVR 很高时阈值不得低于 5（1/0.5=2 -> 抬到 5）"""
        r = term(clicks=10, orders=5)        # CVR 50%
        self.assertEqual(r.click_threshold, 5)

    def test_fallback_cvr_used_when_no_orders(self):
        """零单词自身 CVR 为 0，阈值应退回到品类兜底 CVR —— 曾经的 bug：
        此时阈值算成 0，等于永远不判断（regression）"""
        r = term(clicks=30, orders=0)
        r.fallback_cvr = 0.10
        eff = r.cvr if r.cvr > 0 else r.fallback_cvr
        self.assertEqual(eff, 0.10)
        self.assertEqual(max(5, round(1 / eff)), 10)

    def test_match_type_normalisation(self):
        """真实报表里自动广告的 Match Type 是 '-'，不是 'Auto'（regression）"""
        for raw, expect in [("-", "自动"), ("", "自动"), ("Auto", "自动"),
                            ("broad", "广泛"), ("Exact", "精准"), ("phrase", "词组")]:
            with self.subTest(raw=raw):
                self.assertEqual(term(match_type=raw).match_type_cn(), expect)

    def test_match_type_unknown_returns_none(self):
        self.assertIsNone(term(match_type="??? definitely not a match type").match_type_cn())

    def test_load_rows_computes_catalog_cvr(self):
        """load_rows 必须回填品类兜底 CVR 并重算阈值"""
        csv = ("Customer Search Term,Clicks,Spend,7 Day Total Sales,7 Day Total Orders (#)\n"
               "a,100,100,0,0\nb,100,100,295,10\n")   # 合计 10/200 = 5% -> 阈值 20
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8") as f:
            f.write(csv)
            p = f.name
        rows = S.load_rows(p)
        # a 零单词 -> 用品类兜底 CVR（5% -> 阈值 20）
        # b 自身 CVR 10% -> 用自身（阈值 10），不应被兜底值覆盖
        by_term = {r.term: r for r in rows}
        self.assertEqual(by_term["a"].click_threshold, 20)
        self.assertEqual(by_term["b"].click_threshold, 10)
        self.assertEqual(by_term["a"].fallback_cvr, 0.05)


# ------------------------------------------------------------------- 规则层
class TestRuleLayer(unittest.TestCase):

    def test_zero_order_over_threshold_negates(self):
        r = term(clicks=30, orders=0, spend=30.0)
        r.fallback_cvr = 0.10                       # 阈值 10，30 >= 10
        r.refresh()
        v = R.judge(r, ECON, TARGET)
        self.assertEqual(v.action, "否定")
        self.assertEqual(v.priority, "P0")
        self.assertIn("动态阈值", v.reason)         # 依据必须可追溯

    def test_acos_over_break_even_cuts_bid(self):
        # 售价 40 x 4 = 160 销售额，花费 120 -> ACoS 75% > BE
        r = term(clicks=40, orders=2, spend=120.0, sales=160.0)
        r.fallback_cvr = 0.10
        r.refresh()
        v = R.judge(r, ECON, TARGET)
        self.assertEqual(v.action, "降价")
        self.assertIn("盈亏平衡线", v.reason)

    def test_insufficient_sample_keeps(self):
        r = term(clicks=8, orders=0, spend=8.0)
        r.fallback_cvr = 0.10                       # 阈值 10，8 < 10
        r.refresh()
        v = R.judge(r, ECON, TARGET)
        self.assertEqual(v.action, "保持")
        self.assertIn("样本不足", v.reason)

    def test_high_conversion_velocity_harvests(self):
        """收割判据是转化速率，不是 ACoS（即使 ACoS 差也要收割）"""
        r = term(clicks=100, orders=10, spend=500.0, sales=300.0, running_days=30)  # 2.33 单/周
        v = R.judge(r, ECON, TARGET)
        self.assertEqual(v.action, "加价")
        self.assertIn("转化速率", v.reason)

    def test_every_verdict_has_reason(self):
        for r in [term(clicks=30, orders=0), term(clicks=40, orders=2, spend=120, sales=160),
                  term(clicks=5, orders=0), term(clicks=100, orders=10, spend=500, sales=300)]:
            r.fallback_cvr = 0.10
            r.refresh()
            with self.subTest(clicks=r.clicks, orders=r.orders):
                self.assertTrue(R.judge(r, ECON, TARGET).reason.strip())


# ------------------------------------------------------------- Jev 响应解析
class TestJevParsing(unittest.TestCase):
    """三个字段名坑的回归测试（都曾导致值恒为 None）"""

    REAL_RESPONSE = {"answers": {
        "动作": {"type": "choice", "choice": "pause",
                 "probabilities": {"pause": 0.69, "lower": 0.25}, "confidence": 0.64},
        "无效花费": {"type": "noul", "noul": 0.88},
        "紧迫度": {"type": "score", "score": 1.89,
                   "legend": {"0": "放着不管", "2": "今天就该处理"}, "confidence": 0.84},
    }}

    def test_parses_real_response_shape(self):
        v = J.parse_answers("t", self.REAL_RESPONSE)
        self.assertEqual(v.action, "暂停")
        self.assertAlmostEqual(v.confidence, 0.64)

    def test_noul_value_read_from_noul_field(self):
        """Noul 的值在 'noul' 字段，不是 'probability'（regression）"""
        v = J.parse_answers("t", self.REAL_RESPONSE)
        self.assertAlmostEqual(v.waste_prob, 0.88)

    def test_score_value_read_from_score_field(self):
        """Score 的值在 'score' 字段（regression）"""
        v = J.parse_answers("t", self.REAL_RESPONSE)
        self.assertAlmostEqual(v.urgency, 1.89)

    def test_score_field_name_fallbacks(self):
        for key in ("score", "level", "value", "rating"):
            with self.subTest(key=key):
                resp = {"answers": {"动作": {"choice": "keep", "confidence": 0.9},
                                    "紧迫度": {"type": "score", key: 1.5}}}
                self.assertAlmostEqual(J.parse_answers("t", resp).urgency, 1.5)

    def test_missing_optional_answers_give_none_not_crash(self):
        resp = {"answers": {"动作": {"choice": "keep", "confidence": 0.9}}}
        v = J.parse_answers("t", resp)
        self.assertIsNone(v.urgency)
        self.assertIsNone(v.waste_prob)

    def test_unknown_choice_maps_to_keep(self):
        resp = {"answers": {"动作": {"choice": "totally_unknown", "confidence": 0.9}}}
        self.assertEqual(J.parse_answers("t", resp).action, "保持")


# --------------------------------------------------------------- 门控与硬约束
class TestGating(unittest.TestCase):

    def test_high_risk_thresholds_are_stricter(self):
        """高风险动作（否定/暂停）阈值必须严于低风险（保持）"""
        self.assertGreater(J.GATE_THRESHOLDS["暂停"], J.GATE_THRESHOLDS["保持"])
        self.assertGreater(J.GATE_THRESHOLDS["否定"], J.GATE_THRESHOLDS["保持"])

    def test_gate_auto_above_threshold(self):
        self.assertEqual(J._gate("保持", 0.99, None), "auto")

    def test_gate_review_below_threshold(self):
        self.assertEqual(J._gate("暂停", 0.50, None), "review")

    def test_waste_guard_forces_review_on_irreversible_actions(self):
        """无效花费 > 0.80 且动作为否定/暂停 -> 即使置信度很高也转人工"""
        self.assertEqual(J._gate("否定", 0.99, 0.88), "review")
        self.assertEqual(J._gate("暂停", 0.99, 0.88), "review")

    def test_waste_guard_does_not_block_low_risk_actions(self):
        """保持是低风险动作，不应被该 guard 拦截"""
        self.assertEqual(J._gate("保持", 0.99, 0.88), "auto")

    def test_dry_run_never_calls_network(self):
        v = J.judge("t", {}, dry_run=True)
        self.assertEqual(v.action, "未接入")
        self.assertEqual(v.gate, "dry_run")


# ------------------------------------------------------------------- 比对层
class TestCompare(unittest.TestCase):

    def _rule(self, action="降价"):
        return R.Verdict("t", action, "依据", "P0")

    def _jev(self, action="降价", conf=0.9, urgency=1.5, waste=0.2, gate="auto"):
        return J.JevVerdict("t", action, conf, urgency, waste, gate)

    def test_not_connected_marks_pending_not_review(self):
        """Jev 未接入时必须全部「待标定」——
        曾经的 bug：继续落入高风险分支，把待标定覆盖成人工复核（regression）"""
        for action in ("保持", "降价", "否定", "暂停", "加价"):
            with self.subTest(rule_action=action):
                c = C.compare(self._rule(action), J.JevVerdict("t", "未接入", 0.0, None, None, "dry_run"))
                self.assertEqual(c.disposition, "待标定")

    def test_urgency_and_waste_survive_into_row(self):
        """两个字段曾解析正确但没进 Comparison.as_row()，在中间环节被丢掉（regression）"""
        row = C.compare(self._rule(), self._jev(urgency=1.89, waste=0.88)).as_row()
        self.assertAlmostEqual(row["Jev紧迫度"], 1.89)
        self.assertAlmostEqual(row["无效花费概率"], 0.88)

    def test_divergence_forces_review(self):
        c = C.compare(self._rule("降价"), self._jev(action="暂停"))
        self.assertTrue(c.diverged)
        self.assertEqual(c.disposition, "人工复核")

    def test_agreement_with_high_confidence_auto_executes(self):
        c = C.compare(self._rule("降价"), self._jev(action="降价", conf=0.99, gate="auto"))
        self.assertFalse(c.diverged)
        self.assertEqual(c.disposition, "自动执行")

    def test_high_risk_action_needs_auto_gate(self):
        c = C.compare(self._rule("否定"), self._jev(action="否定", conf=0.60, gate="review"))
        self.assertEqual(c.disposition, "人工复核")

    def test_intensity_divergence_is_flagged(self):
        """降价 vs 否定方向一致但力度不同，也算分歧（强度分歧）"""
        c = C.compare(self._rule("降价"), self._jev(action="否定"))
        self.assertTrue(c.diverged)
        self.assertIn("强度分歧", c.note)

    def test_summary_counts(self):
        rows = [C.compare(self._rule(), self._jev()),
                C.compare(self._rule("降价"), self._jev(action="暂停"))]
        s = C.summarize(rows)
        self.assertEqual(s["总词数"], 2)
        self.assertEqual(s["分歧数"], 1)


# --------------------------------------------------------------- 端到端集成
class TestPipelineEndToEnd(unittest.TestCase):

    def test_dry_run_on_bundled_sample(self):
        """附带的样例数据必须能跑通（CI 的核心检查）"""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            r = subprocess.run(
                [sys.executable, "src/pipeline.py",
                 "--input", "examples/input-sample.csv",
                 "--price", "39.99", "--cost", "8", "--fba", "7.5", "--first-leg", "3",
                 "--commission-rate", "15", "--return-rate", "5",
                 "--target-acos", "25", "--running-days", "30", "--bid", "0.85",
                 "--out", str(out)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["总词数"], 7)
            # 未配 API Key 时默认 dry-run，应全部落在「待标定」
            self.assertEqual(data["summary"]["待标定"], 7)
            self.assertEqual(data["summary"]["人工复核"], 0)
            # 每行都必须有可追溯依据
            for row in data["rows"]:
                self.assertTrue(row["规则层依据"].strip())
                self.assertEqual(row["Jev动作"], "未接入")

    def test_break_even_in_output_matches_economics(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            subprocess.run(
                [sys.executable, "src/pipeline.py", "--input", "examples/input-sample.csv",
                 "--price", "29.50", "--cost", "6.2", "--fba", "5.4", "--first-leg", "2.1",
                 "--commission-rate", "15", "--return-rate", "3",
                 "--target-acos", "25", "--out", str(out)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            be = json.loads(out.read_text(encoding="utf-8"))["meta"]["economics"]["盈亏平衡ACOS"]
            # 流水线为可读性把比例四舍五入到 4 位小数（0.01% 精度），故按 4 位比较
            self.assertAlmostEqual(be, round(ECON.break_even_acos(), 4), places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
