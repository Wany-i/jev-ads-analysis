"""流水线编排：广告导出 -> 规则层 -> Jev 影子层 -> 比对 -> 输出。

    python src/pipeline.py --input examples/input-sample.csv \
        --price 39.99 --cost 8 --fba 7.5 --first-leg 3 \
        --commission-rate 15 --return-rate 5 \
        --target-acos 25 --out output/pipeline-output.json

默认 `--jev dry-run`：不联网、不消耗额度，输出「未接入」。
标定完成后加 `--jev live` 才会真正调用（需要 OPENROUTER_API_KEY）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许 `python src/pipeline.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare import compare, summarize              # noqa: E402
from jev_shadow import judge_all as jev_judge_all   # noqa: E402
from rule_layer import judge_all as rule_judge_all  # noqa: E402
from state_builder import Economics, load_rows, MATCH_TYPE_CN  # noqa: E402
import clipboard_io                                  # noqa: E402


def run(args: argparse.Namespace) -> dict:
    econ = Economics(
        price=args.price,
        cost=args.cost,
        fba=args.fba,
        first_leg=args.first_leg,
        commission_rate=args.commission_rate / 100.0,
        return_rate=args.return_rate / 100.0,
    )
    target = args.target_acos / 100.0

    # 取数：文件路径，或剪贴板（RPA 直接复制报表的场景）
    src = args.input
    from_clipboard = str(args.input).lower() in ("clipboard", "clip")
    if from_clipboard:
        text = clipboard_io.read_text()
        if not text.strip():
            raise ValueError("剪贴板为空")
        if not clipboard_io.looks_like_table(text):
            preview = text.strip().splitlines()[0][:120] if text.strip() else ""
            raise ValueError(f"剪贴板内容不像表格数据（首行: {preview}）")
        import datetime as _dt
        delim = clipboard_io.sniff_delimiter(text)
        ext = ".tsv" if delim == chr(9) else ".csv"
        # 先把剪贴板快照落盘再分析。
        # 原因：--out-clipboard 会把结果写回剪贴板，覆盖输入数据。
        # 落盘后输入可复现，也能直接喂给 --input <快照文件> 重跑。
        snap_dir = Path("output"); snap_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        snap = snap_dir / f"clipboard-input-{stamp}{ext}"
        snap.write_text(text, encoding="utf-8")
        print(f"剪贴板输入已快照: {snap}")
        src = str(snap)

    rows = load_rows(src, running_days=args.running_days, current_bid=args.bid)
    if not rows:
        raise SystemExit("输入里没有有效数据行")

    # 1) 规则层（主路径，可追溯）
    rules = rule_judge_all(rows, econ, target)

    # 2) Jev 影子层
    states = [{"term": r.term, "state": r.to_jev_state(econ, target)} for r in rows]
    jevs = jev_judge_all(states, dry_run=(args.jev != "live"))
    jev_by_term = {j.term: j for j in jevs}

    # 3) 比对
    comps = [compare(v, jev_by_term[v.term]) for v in rules]

    # 4) 组装输出
    out_rows = []
    for r, c in zip(rows, comps):
        row = {
            "关键词": r.term,
            "站点": "US",
                # 归一成中文，与多维表格的 select 选项对齐
                "匹配方式": MATCH_TYPE_CN.get((r.match_type or "").lower(), r.match_type or "未知"),
            "曝光": int(r.impressions),
            "点击": int(r.clicks),
            "花费": round(r.spend, 2),
            "销售额": round(r.sales, 2),
            "订单": int(r.orders),
            "运行天数": r.running_days,
            "当前出价": round(r.current_bid, 2),
            "目标ACOS": round(target, 4),
            "实测ACOS": round(r.acos, 4) if r.acos >= 0 else None,
            "盈亏平衡ACOS": round(econ.break_even_acos(), 4),
            "可接受CPC": round(econ.affordable_cpc(r.cvr, target), 4) if r.cvr > 0 else None,
            "动态点击阈值": r.click_threshold,
            "CVR": round(r.cvr, 4),
            "CPC": round(r.cpc, 4),
        }
        row.update(c.as_row())
        out_rows.append(row)

    return {
        "meta": {
            "input": "clipboard" if from_clipboard else str(args.input),
            "jev_mode": args.jev,
            "economics": {
                "售价": econ.price, "成本": econ.cost, "FBA": econ.fba,
                "头程": econ.first_leg, "佣金率": econ.commission_rate,
                "退货率": econ.return_rate,
                "广告前单位贡献": round(econ.contribution(), 4),
                "盈亏平衡ACOS": round(econ.break_even_acos(), 4),
                "目标ACOS": round(target, 4),
            },
        },
        "summary": summarize(comps),
        "rows": out_rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Jev 驱动的广告优化分析参考实现")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="output/pipeline-output.json")
    # 单位经济学
    ap.add_argument("--price", type=float, required=True, help="售价")
    ap.add_argument("--cost", type=float, default=0.0)
    ap.add_argument("--fba", type=float, default=0.0)
    ap.add_argument("--first-leg", type=float, default=0.0, dest="first_leg")
    ap.add_argument("--commission-rate", type=float, default=15.0, dest="commission_rate",
                    help="平台佣金率 %%（默认 15）")
    ap.add_argument("--return-rate", type=float, default=0.0, dest="return_rate",
                    help="退货率 %%（默认 0）")
    # 目标与假设
    ap.add_argument("--target-acos", type=float, default=25.0, dest="target_acos",
                    help="目标 ACoS %%（默认 25）")
    ap.add_argument("--running-days", type=int, default=30, dest="running_days")
    ap.add_argument("--bid", type=float, default=0.0, help="当前出价（报表无此列时的兜底）")
    # Jev
    ap.add_argument("--out-clipboard", action="store_true", dest="out_clipboard",
                    help="把结果摘要写回剪贴板")
    ap.add_argument("--jev", choices=["dry-run", "live"], default="dry-run",
                    help="dry-run（默认，不联网）| live（需 OPENROUTER_API_KEY）")
    args = ap.parse_args(argv)

    try:
        result = run(args)
    except (ValueError, RuntimeError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["summary"]
    print(f"输入行数: {s['总词数']}　已接入 Jev: {s['已接入 Jev 词数']}　"
          f"分歧: {s['分歧数']}　自动执行: {s['自动执行']}　人工复核: {s['人工复核']}　"
          f"待标定: {s['待标定']}")
    print(f"输出: {out}")
    if getattr(args, "out_clipboard", False):
        # 安全网：写回剪贴板前，先把剪贴板原有内容备份。
        # 血泪教训：--out-clipboard 会覆盖用户当前剪贴板里的任何东西
        # （实测时覆盖掉了用户放在剪贴板里的 API 凭据）。
        try:
            prev = clipboard_io.read_text()
        except Exception:
            prev = ""
        if prev.strip():
            import datetime as _dt
            bdir = Path("output"); bdir.mkdir(parents=True, exist_ok=True)
            bpath = bdir / f"clipboard-before-write-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
            bpath.write_text(prev, encoding="utf-8")
            print(f"⚠️  剪贴板原有内容已备份到 {bpath}（即将被结果覆盖）")

        lines = ["关键词\t规则层动作\t优先级\t依据"]
        for r in result["rows"]:
            lines.append(f'{r["关键词"]}\t{r["规则层动作"]}\t{r["规则层优先级"]}\t{r["规则层依据"]}')
        clipboard_io.write_text("\n".join(lines))
        print("结果摘要已写回剪贴板")
    return 0


if __name__ == "__main__":
    sys.exit(main())

