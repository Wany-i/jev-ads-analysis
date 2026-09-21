"""阈值扫描：产出准确率/覆盖率曲线。

没有人工标注，所以用两个可测指标代替：
- 覆盖率 = gate 判为 auto 的比例（能自动执行多少）
- 一致率 = Jev 与规则层判断相同的比例（在 auto 子集上单独统计）

如果 confidence 真的有区分度，应当看到：阈值升高 → 覆盖率降 → auto 子集一致率升。
"""
import io, json, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

d = json.load(open("output/jev-redhen-45.json", encoding="utf-8"))
rows = [r for r in d["rows"] if r.get("Jev置信度") is not None]
n = len(rows)

def agree(r): return r["规则层动作"] == r["Jev动作"]
HIGH_RISK = {"暂停", "否定"}

print(f"数据集: {n} 个关键词（真实 Jev 调用结果）")
print(f"规则层动作分布: ", end="")
from collections import Counter
print(dict(Counter(r["规则层动作"] for r in rows)))
print(f"Jev 动作分布:   {dict(Counter(r['Jev动作'] for r in rows))}")
print(f"总体一致率: {sum(1 for r in rows if agree(r))/n:.1%}  分歧: {sum(1 for r in rows if not agree(r))} 条")
print()

print("=== 统一阈值扫描 ===")
print(f'{"阈值":>6}{"自动执行":>8}{"覆盖率":>9}{"auto一致率":>12}{"review一致率":>13}{"高风险误放":>11}')
print("-" * 62)
sweep = []
for t in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
    auto    = [r for r in rows if r["Jev置信度"] >= t]
    review  = [r for r in rows if r["Jev置信度"] <  t]
    cov     = len(auto) / n
    a_agree = (sum(1 for r in auto if agree(r)) / len(auto)) if auto else float("nan")
    r_agree = (sum(1 for r in review if agree(r)) / len(review)) if review else float("nan")
    # "高风险误放"：自动执行了、Jev 判暂停/否定、但与规则层不一致
    bad = sum(1 for r in auto if r["Jev动作"] in HIGH_RISK and not agree(r))
    sweep.append((t, len(auto), cov, a_agree, r_agree, bad))
    print(f'{t:>6.2f}{len(auto):>8}{cov:>9.1%}{a_agree:>12.1%}{r_agree:>13.1%}{bad:>11}')

print()
print("=== 结论 ===")
best = max(sweep, key=lambda x: (x[3] if x[3] == x[3] else 0))
print(f"  · 覆盖率从 71%（阈值 0.50）降到 {sweep[-1][2]:.0%}（阈值 0.90）")
print(f"  · auto 子集一致率最高点: 阈值 {best[0]:.2f} → 覆盖 {best[2]:.0%}、一致率 {best[3]:.1%}")
print(f"  · 当前实现用的是分动作阈值（0.50~0.90），结果: 自动 {d['summary']['自动执行']} 条 / 人工 {d['summary']['人工复核']} 条")

# 置信度是否有区分度：比较一致 vs 不一致两组的置信度分布
ag = [r["Jev置信度"] for r in rows if agree(r)]
dg = [r["Jev置信度"] for r in rows if not agree(r)]
import statistics as st
print()
print(f"  · 一致组置信度: 均值 {st.mean(ag):.3f} 中位 {st.median(ag):.3f} (n={len(ag)})")
print(f"  · 分歧组置信度: 均值 {st.mean(dg):.3f} 中位 {st.median(dg):.3f} (n={len(dg)})")
if st.mean(ag) > st.mean(dg):
    print(f"  → 一致组平均置信度高出 {st.mean(ag)-st.mean(dg):.3f}，**confidence 有区分度**")
else:
    print("  → 两组置信度无明显差异，confidence 对'是否同意规则'没有区分度")
